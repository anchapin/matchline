"""Read-only 3D review viewer (#1001), shared with the export package.

``matchline viewer model.json --out viewer.html`` renders the canonical
model as one self-contained HTML page: a read-only 3D view of the final
simplified model with pan/rotate/zoom, level-by-level isolation, a top-down
orthographic view per level, and toggleable overlays (floor plans, facade
elevations, per-element confidence heat-mapping, zone assignment). The
export package's ``share_back.html`` embeds the same render core (#999).

Tech choice, decided and documented in ``docs/review_viewer.md``: the
viewer is self-contained HTML (inline CSS/JS, no external assets), the
same offline approach the review report (#749) and the share-back preview
(#999) already use. matchline-ui (#987/#988) stays the interactive editor
home; the review viewer deliberately adds no editing.

The invariant the acceptance names is kept structurally: the viewer
renders the final simplified model FROM the canonical model the export
writes. ``build_scene`` reads the model object and copies its geometry
verbatim into the scene (polygons, wall endpoints, heights, confidences);
it re-derives nothing, transforms nothing, and writes no second geometry
file. The browser draws exactly what the model states.
"""

from __future__ import annotations

import html
import json
from typing import List, Optional

import corrections

SCHEMA = "matchline.review_viewer/1"

FACADES = ("south", "north", "east", "west")

# Confidence buckets for the heat-mapping overlay. The thresholds mirror the
# auto-accept threshold story: green is settled fact, amber is worth a look,
# red is where errors most likely live.
HEAT = ((0.9, "#2e7d32"), (0.7, "#f9a825"), (0.0, "#c62828"))

_STYLE = """
*{box-sizing:border-box}body{font:12px/1.4 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;
color:#111;margin:0;background:#f4f4f2}
#bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:8px 10px;background:#fff;
border-bottom:1px solid #ccc}
#bar h1{font-size:14px;margin:0 10px 0 0}
#bar button,#bar select{font:12px/1.4 inherit;padding:3px 8px;border:1px solid #999;background:#fff;
border-radius:3px;cursor:pointer}
#bar button.on{background:#1f4e79;color:#fff;border-color:#1f4e79}
#stage{position:relative}
canvas{display:block;background:#fdfdfb;cursor:grab}
#side{position:absolute;top:8px;right:8px;background:rgba(255,255,255,.94);border:1px solid #ccc;
padding:8px 10px;max-width:240px;font-size:11px}
#side h2{font-size:12px;margin:2px 0 4px}
#side .row{margin:3px 0}
.sw{display:inline-block;width:10px;height:10px;margin-right:4px;vertical-align:-1px}
#status{position:absolute;left:8px;bottom:8px;background:rgba(255,255,255,.92);
border:1px solid #ccc;padding:4px 8px;font-size:11px;color:#444}
.banner{background:#fff4f4;border:1px solid #b00;padding:6px 8px;margin:0}
.note{color:#555;font-size:10px;margin-top:6px}
#corrpanel{margin-top:8px;border-top:1px solid #ddd;padding-top:6px}
#corrpanel h2{font-size:12px;margin:2px 0 4px}
#corrpanel .row{margin:4px 0}
#corrpanel input{font:11px/1.2 inherit;padding:2px 4px;border:1px solid #999;width:100px}
#corrpanel button{font:11px/1.2 inherit;padding:2px 6px;border:1px solid #999;background:#fff;
border-radius:3px;cursor:pointer}
.c-msg{color:#b00;font-size:10px;margin:3px 0}
.c-hist{color:#555;font-size:10px}
"""

_BODY = """
<div id="bar"><h1>Review viewer</h1>
<button id="v-iso" class="on">3D</button><button id="v-top">Top</button>
<select id="v-level"><option value="all">All levels</option></select>
<span style="width:14px"></span>
<button id="f-south">South</button><button id="f-north">North</button>
<button id="f-east">East</button><button id="f-west">West</button>
<span style="width:14px"></span>
<button id="t-heat">Heat map</button><button id="t-zones">Zones</button>
<button id="t-env" class="on">Envelope</button><button id="t-plan">Plans</button>
<button id="t-elev">Elevations</button><button id="t-rev">Review marks</button>
<button id="t-mep">MEP symbols</button><button id="t-loop">Air loops</button>
<button id="t-corr">Correct</button>
</div>
<div id="stage"><canvas id="cv"></canvas>
<div id="side"><h2>Legend</h2><div id="legend"></div><div id="revlist"></div>
<div id="corrpanel"></div>
<div class="note">Read-only: renders the canonical model the export wrote,
never a second copy of its geometry. Overlays come from the same model.</div></div>
<div id="status"></div></div>
"""

# The 3D renderer, vanilla JS on a 2D canvas with an orthographic
# projection: yaw rotates the model, elev tilts from side (0) to top-down
# (90), painter's algorithm on face depth. Overlay logic reads the same
# scene the report and the package embed.
_JS = r"""
var S = __SCENE__;
var cv = document.getElementById("cv"), ctx = cv.getContext("2d");
var cam = {yaw: 0, elev: 35 * Math.PI / 180, zoom: 8, px: 0, py: 0};
var level = "all", facade = null;
var on = {heat: false, zones: false, env: true, plan: false, elev: false, rev: false,
  mep: false, loop: false};
var ZPAL = ["#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd","#8c564b",
"#e377c2","#7f7f7f","#bcbd22","#17becf","#aec7e8","#ffbb78"];
var FCOL = {south: "#c62828", north: "#1565c0", east: "#2e7d32", west: "#6a1b9a"};
var MPAL = __MEPCOLORS__;
function zcol(zid){var h=0;for(var i=0;i<zid.length;i++)h=(h*31+zid.charCodeAt(i))>>>0;
  return ZPAL[h%ZPAL.length];}
function heat(c){if(c==null)return null;
  for(var i=0;i<HEAT.length;i++)if(c>=HEAT[i][0])return HEAT[i][1];return HEAT[HEAT.length-1][1];}
var HEAT = __HEAT__;
var lvlById = {}; S.levels.forEach(function(l){lvlById[l.id]=l;});
var spById = {}; S.spaces.forEach(function(s){spById[s.id]=s;});
function lvl(id){return lvlById[id] || {elevation_z_m: 0, wall_height_m: 3.0};}
function wh(s,l){return s.height_m || s.ceiling_height_m || l.wall_height_m || 3.0;}
function visible(it){return level === "all" || it.level === level || it.space_level === level
  || (it.level_id && it.level_id === level);}
function buildFaces(){
  var fl = [], wl = [], env = [];
  S.spaces.forEach(function(s){
    if(!visible(s) || !s.polygon || s.polygon.length < 3) return;
    var l = lvl(s.level), z0 = l.elevation_z_m, h = wh(s, l);
    fl.push({pts: s.polygon.map(function(p){return [p[0], p[1], z0];}), kind: "floor", el: s,
      z: z0});
    for(var i = 0; i < s.polygon.length; i++){
      var a = s.polygon[i], b = s.polygon[(i + 1) % s.polygon.length];
      wl.push({pts: [[a[0], a[1], z0], [b[0], b[1], z0], [b[0], b[1], z0 + h],
        [a[0], a[1], z0 + h]], kind: "wall", el: s});
    }
  });
  S.envelope.forEach(function(w){
    if(!visible(w)) return;
    var l = lvl(w.space_level), z0 = l.elevation_z_m;
    var h = w.height_m || l.wall_height_m || 3.0;
    env.push({pts: [[w.from[0], w.from[1], z0], [w.to[0], w.to[1], z0],
      [w.to[0], w.to[1], z0 + h], [w.from[0], w.from[1], z0 + h]], kind: "env", el: w});
  });
  return {fl: fl, wl: wl, env: env};
}
var faces = null;
function proj(p){
  var cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw);
  var ce = Math.cos(cam.elev), se = Math.sin(cam.elev);
  var rx = p[0] * cy - p[1] * sy, ry = p[0] * sy + p[1] * cy;
  return {x: rx, y: ry * se - p[2] * ce, d: ry * ce + p[2] * se};
}
function drawPoly(pts, fill, stroke, w){
  ctx.beginPath();
  pts.forEach(function(q, i){var s = proj(q);
    if(i === 0) ctx.moveTo(s.x * cam.zoom + cam.px, s.y * cam.zoom + cam.py);
    else ctx.lineTo(s.x * cam.zoom + cam.px, s.y * cam.zoom + cam.py);});
  ctx.closePath();
  if(fill){ctx.globalAlpha = 0.85; ctx.fillStyle = fill; ctx.fill(); ctx.globalAlpha = 1;}
  if(stroke){ctx.strokeStyle = stroke; ctx.lineWidth = w || 1; ctx.stroke();}
}
function floorColor(s){
  if(on.zones && s.zones && s.zones.length) return zcol(s.zones[0]);
  if(on.heat) return heat(s.confidence) || "#e8e8e4";
  return "#e8e8e4";
}
function envColor(w){
  if(on.heat) return heat(w.confidence) || "#777";
  return FCOL[w.facade] || "#1f4e79";
}
function render(){
  if(!faces) faces = buildFaces();
  var W = cv.width = window.innerWidth, H = cv.height = window.innerHeight;
  ctx.clearRect(0, 0, W, H);
  var all = faces.fl.concat(faces.wl, faces.env);
  all.forEach(function(f){f._d = proj([f.pts[0][0], f.pts[0][1], f.pts[0][2]]).d;});
  all.sort(function(a, b){return b._d - a._d;});
  all.forEach(function(f){
    if(f.kind === "floor") drawPoly(f.pts, floorColor(f.el), "#999", 1);
    else if(f.kind === "wall") drawPoly(f.pts, "#fbfbf8", "#bbb", 1);
    else drawPoly(f.pts, envColor(f.el), envColor(f.el), 1.5);
  });
  if(on.elev && facade && cam.elev < 0.35) drawOpenings();
  if(on.plan && cam.elev > 1.4) drawPlan();
  if(on.rev) drawReviewMarks();
  if(on.mep) drawMEP();
  if(on.loop) drawLoops();
  status();
}
function drawOpenings(){
  // Openings placed by host_interval_m, absolute along the facade axis from
  // the axis minimum of that level+facade's envelope walls (documented).
  var axis = (facade === "south" || facade === "north") ? 0 : 1;
  var mins = {}, plane = {}, n = {};
  S.envelope.forEach(function(w){
    if(w.facade !== facade || !visible(w)) return;
    var a = Math.min(w.from[axis], w.to[axis]);
    if(mins[w.space_level] === undefined || a < mins[w.space_level]) mins[w.space_level] = a;
    plane[w.space_level] = (plane[w.space_level] || 0)
      + (w.from[1 - axis] + w.to[1 - axis]) / 2;
    n[w.space_level] = (n[w.space_level] || 0) + 1;
  });
  Object.keys(plane).forEach(function(k){plane[k] = plane[k] / n[k];});
  S.openings.forEach(function(o){
    if(o.facade !== facade || !o.interval || !visible(o)) return;
    var l = lvl(o.space_level), z0 = l.elevation_z_m;
    var s0 = mins[o.space_level] + o.interval[0], s1 = mins[o.space_level] + o.interval[1];
    var cross = plane[o.space_level] != null ? plane[o.space_level] : o.cross;
    var sill = o.sill == null ? 0.9 : o.sill, head = o.head == null ? sill + o.height : o.head;
    var q = axis === 0
      ? [[s0, cross, z0 + sill], [s1, cross, z0 + sill],
         [s1, cross, z0 + head], [s0, cross, z0 + head]]
      : [[cross, s0, z0 + sill], [cross, s1, z0 + sill],
         [cross, s1, z0 + head], [cross, s0, z0 + head]];
    drawPoly(q, null, FCOL[facade], 2);
  });
}
function drawPlan(){
  ctx.font = "10px sans-serif";
  S.spaces.forEach(function(s){
    if(!visible(s) || !s.polygon || s.polygon.length < 3) return;
    var cx = 0, cy = 0;
    s.polygon.forEach(function(p){cx += p[0]; cy += p[1];});
    cx /= s.polygon.length; cy /= s.polygon.length;
    var p = proj([cx, cy, lvl(s.level).elevation_z_m]);
    ctx.fillStyle = "#222";
    ctx.fillText(s.label, p.x * cam.zoom + cam.px, p.y * cam.zoom + cam.py);
  });
}
var revTargets = null;
function drawReviewMarks(){
  if(!revTargets){revTargets = {};
    (S.review_targets || []).forEach(function(t){revTargets[t] = 1;});}
  faces.fl.forEach(function(f){
    if(!revTargets[f.el.id]) return;
    drawPoly(f.pts, null, "#c62828", 2.5);
  });
}
function drawMEP(){
  // MEP symbols read verbatim from the model's component instances (#1004).
  (S.mep ? S.mep.symbols : []).forEach(function(s){
    if(!visible(s)) return;
    var l = lvl(s.level);
    var p = proj([s.x, s.y, l.elevation_z_m + 1.2]);
    var X = p.x * cam.zoom + cam.px, Y = p.y * cam.zoom + cam.py;
    ctx.fillStyle = MPAL[s.type] || "#555";
    ctx.beginPath();
    ctx.moveTo(X, Y - 4); ctx.lineTo(X + 4, Y);
    ctx.lineTo(X, Y + 4); ctx.lineTo(X - 4, Y);
    ctx.closePath(); ctx.fill();
    ctx.font = "9px sans-serif"; ctx.fillStyle = "#222";
    ctx.fillText(s.tag || s.id, X + 6, Y + 3);
  });
}
function zoneCentroid(zid){
  var sx = 0, sy = 0, z0 = null, n = 0;
  S.spaces.forEach(function(s){
    if(s.zones && s.zones.indexOf(zid) >= 0 && s.polygon && s.polygon.length >= 3){
      var cx = 0, cy = 0;
      s.polygon.forEach(function(p){cx += p[0]; cy += p[1];});
      sx += cx / s.polygon.length; sy += cy / s.polygon.length;
      z0 = lvl(s.level).elevation_z_m; n++;
    }
  });
  return n ? [sx / n, sy / n, z0] : null;
}
function drawLoops(){
  // Air-loop traces: the zone lists the model records per air loop, joined
  // through the zones already in the scene. Nothing re-derived here.
  (S.mep ? S.mep.air_loops : []).forEach(function(a){
    var pts = (a.zones || []).map(zoneCentroid).filter(Boolean);
    if(!pts.length) return;
    ctx.strokeStyle = "#e65100"; ctx.lineWidth = 2; ctx.setLineDash([6, 4]);
    for(var i = 0; i + 1 < pts.length; i++){
      var p0 = proj(pts[i]), p1 = proj(pts[i + 1]);
      ctx.beginPath();
      ctx.moveTo(p0.x * cam.zoom + cam.px, p0.y * cam.zoom + cam.py);
      ctx.lineTo(p1.x * cam.zoom + cam.px, p1.y * cam.zoom + cam.py);
      ctx.stroke();
    }
    ctx.setLineDash([]);
    var f = proj(pts[0]);
    ctx.font = "10px sans-serif"; ctx.fillStyle = "#e65100";
    ctx.fillText(a.tag, f.x * cam.zoom + cam.px + 6, f.y * cam.zoom + cam.py - 6);
  });
}
function fit(){
  if(!faces) faces = buildFaces();
  var xs = [], ys = [];
  var src = faces.fl.length ? faces.fl : [{pts: [[0, 0, 0]]}];
  src.forEach(function(f){f.pts.forEach(function(p){
    var s = proj([p[0], p[1], p[2]]); xs.push(s.x); ys.push(s.y);});});
  if(!xs.length) return;
  var w = Math.max.apply(null, xs) - Math.min.apply(null, xs);
  var h = Math.max.apply(null, ys) - Math.min.apply(null, ys);
  cam.zoom = Math.min((window.innerWidth - 300) / Math.max(w, 0.001),
    (window.innerHeight - 120) / Math.max(h, 0.001));
  cam.px = window.innerWidth / 2 - ((Math.max.apply(null, xs)
    + Math.min.apply(null, xs)) / 2) * cam.zoom;
  cam.py = window.innerHeight / 2 - ((Math.max.apply(null, ys)
    + Math.min.apply(null, ys)) / 2) * cam.zoom;
}
function setLevel(id){
  level = id; faces = null; revTargets = null; fit(); render();
  document.querySelectorAll("#v-level option").forEach(function(o){
    o.selected = o.value === id;});
}
function view(yaw, elev, f){
  cam.yaw = yaw; cam.elev = elev; facade = f || null; fit(); render();
  ["v-iso", "v-top"].forEach(function(id){
    document.getElementById(id).className = "";});
  if(elev >= 1.5){document.getElementById("v-top").className = "on";}
  else if(!f){document.getElementById("v-iso").className = "on";}
  ["f-south", "f-north", "f-east", "f-west"].forEach(function(id){
    document.getElementById(id).className = ("f-" + f) === id ? "on" : "";});
}
function toggle(id, key){
  on[key] = !on[key];
  document.getElementById(id).className = on[key] ? "on" : "";
  render();
}
function bind(){
  document.getElementById("v-iso").onclick = function(){view(0, 35 * Math.PI / 180, null);};
  document.getElementById("v-top").onclick = function(){view(0, Math.PI / 2, null);};
  ["south", "north", "east", "west"].forEach(function(f, i){
    document.getElementById("f-" + f).onclick = function(){
      view([0, Math.PI, -Math.PI / 2, Math.PI / 2][i], 0, f);};
  });
  [["t-heat", "heat"], ["t-zones", "zones"], ["t-env", "env"],
   ["t-plan", "plan"], ["t-elev", "elev"], ["t-rev", "rev"],
   ["t-mep", "mep"], ["t-loop", "loop"]].forEach(function(p){
    document.getElementById(p[0]).onclick = function(){toggle(p[0], p[1]);};
  });
  var sel = document.getElementById("v-level");
  S.levels.forEach(function(l){
    var o = document.createElement("option"); o.value = l.id;
    o.textContent = l.name ? l.id + " " + l.name : l.id; sel.appendChild(o);});
  sel.onchange = function(){setLevel(sel.value);};
  var drag = null;
  cv.onmousedown = function(e){drag = {x: e.clientX, y: e.clientY,
    sx: e.clientX, sy: e.clientY, rot: !e.shiftKey};};
  window.onmouseup = function(e){
    if(drag && corr.on && Math.abs(e.clientX - drag.sx) < 4
      && Math.abs(e.clientY - drag.sy) < 4) corrClick(e);
    drag = null;};
  cv.onmousemove = function(e){
    if(!drag) return;
    if(drag.rot){cam.yaw += (e.clientX - drag.x) * 0.008;
      cam.elev = Math.max(-0.1, Math.min(Math.PI / 2, cam.elev + (e.clientY - drag.y) * 0.008));
      facade = null; document.querySelectorAll("#bar button[id^=f-]").forEach(function(b){
        b.className = "";});}
    else{cam.px += e.clientX - drag.x; cam.py += e.clientY - drag.y;}
    drag.x = e.clientX; drag.y = e.clientY; render();};
  cv.onwheel = function(e){e.preventDefault();
    var f = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    cam.zoom *= f; cam.px = e.clientX - (e.clientX - cam.px) * f;
    cam.py = e.clientY - (e.clientY - cam.py) * f; render();};
}
function status(){
  var sp = S.spaces.filter(visible).length;
  var env = S.envelope.filter(visible).length;
  var t = "spaces " + sp + " · envelope walls " + env + " · open review items "
    + S.review_open + " · " + (level === "all" ? "all levels" : "level " + level);
  if(on.mep && S.mep) t += " · MEP symbols " + S.mep.symbols.length;
  if(facade) t += " · " + facade + " elevation (elevations overlay places openings by "
    + "host_interval_m from the facade axis minimum)";
  if(corr.on) t += " · correct mode: " + corr.ops.length + " op(s) queued";
  document.getElementById("status").textContent = t;
}
function legend(){
  var lg = document.getElementById("legend"), h = "";
  h += '<div class="row"><span class="sw" style="background:#2e7d32"></span>confidence ≥ 0.9</div>';
  h += '<div class="row"><span class="sw" style="background:#f9a825"></span>0.7 – 0.9</div>';
  h += '<div class="row"><span class="sw" style="background:#c62828"></span>&lt; 0.7</div>';
  if(on.zones && S.zones.length){
    h += "<h2>Zones</h2>";
    S.zones.forEach(function(z){
      h += '<div class="row"><span class="sw" style="background:' + zcol(z.id)
        + '"></span>' + z.id + (z.air_loop ? " · " + z.air_loop : "") + "</div>";});
  }
  if(on.mep && S.mep && S.mep.symbols.length){
    h += "<h2>MEP symbols</h2>";
    var types = {};
    S.mep.symbols.forEach(function(s){types[s.type] = (types[s.type] || 0) + 1;});
    Object.keys(types).forEach(function(t){
      h += '<div class="row"><span class="sw" style="background:' + (MPAL[t] || "#555")
        + '"></span>' + t + " × " + types[t] + "</div>";});
  }
  if(on.loop && S.mep && S.mep.air_loops.length){
    h += "<h2>Air loops</h2>";
    S.mep.air_loops.forEach(function(a){
      h += '<div class="row"><span class="sw" style="background:#e65100"></span>'
        + a.tag + " · " + a.zones.length + " zone(s)</div>";});
  }
  lg.innerHTML = h;
}

// ------------------------------------------------- correction mode (#1002)
// The viewer stays read-only over the model: Correct mode collects ops
// (rename, reclassify, merge, split, revert-to-auto) into one
// viewer_corrections.json the reviewer saves and the CLI applies through the
// decisions workflow. It never edits the model itself.
var corr = {on: false, ops: [], selS: null, selO: null, arm: null, armPts: [], msg: ""};
var corrPanel = document.getElementById("corrpanel");
document.getElementById("t-corr").onclick = function(){
  corr.on = !corr.on;
  document.getElementById("t-corr").className = corr.on ? "on" : "";
  if(!corr.on){corr.selS = null; corr.selO = null;
    corr.arm = null; corr.armPts = []; corr.msg = "";}
  corrPanelUI(); render();
};
function esc(s){return String(s === null || s === undefined ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;");}
function unproj(sx, sy, z){
  var u = (sx - cam.px) / cam.zoom, v = (sy - cam.py) / cam.zoom;
  var cy = Math.cos(cam.yaw), sy2 = Math.sin(cam.yaw);
  var se = Math.sin(cam.elev), ce = Math.cos(cam.elev);
  if(se < 0.08) return null;
  var rx = u, ry = (v + z * ce) / se;
  return [rx * cy + ry * sy2, -rx * sy2 + ry * cy];
}
function inPoly(poly, x, y){
  var inside = false;
  for(var i = 0, j = poly.length - 1; i < poly.length; j = i++){
    var xi = poly[i][0], yi = poly[i][1], xj = poly[j][0], yj = poly[j][1];
    if(((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}
function snapBoundary(poly, x, y){
  var best = null, bd = 1e18;
  for(var i = 0; i < poly.length; i++){
    var a = poly[i], b = poly[(i + 1) % poly.length];
    var dx = b[0] - a[0], dy = b[1] - a[1], L2 = dx * dx + dy * dy;
    var t = L2 ? Math.max(0, Math.min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / L2)) : 0;
    var px = a[0] + t * dx, py = a[1] + t * dy;
    var d = (px - x) * (px - x) + (py - y) * (py - y);
    if(d < bd){bd = d; best = [Math.round(px * 1000) / 1000, Math.round(py * 1000) / 1000];}
  }
  return best;
}
function opCenters(){
  var A = {south: 0, north: 0, east: 1, west: 1}, mins = {}, plane = {}, n = {};
  S.envelope.forEach(function(w){
    var a = A[w.facade]; if(a === undefined || !visible(w)) return;
    var k = w.space_level + "/" + w.facade;
    var lo = Math.min(w.from[a], w.to[a]);
    if(mins[k] === undefined || lo < mins[k]) mins[k] = lo;
    plane[k] = (plane[k] || 0) + (w.from[1 - a] + w.to[1 - a]) / 2;
    n[k] = (n[k] || 0) + 1;
  });
  var out = {};
  S.openings.forEach(function(o){
    if(!o.interval || !visible(o)) return;
    var a = A[o.facade]; if(a === undefined) return;
    var k = o.space_level + "/" + o.facade;
    if(mins[k] === undefined) return;
    var c = [0, 0];
    c[a] = mins[k] + (o.interval[0] + o.interval[1]) / 2;
    c[1 - a] = plane[k] / n[k];
    out[o.id] = c;
  });
  return out;
}
function corrClick(e){
  if(!corr.on) return;
  if(level === "all"){corr.msg = "pick a level (the level selector) first"; corrPanelUI(); return;}
  var z = lvl(level).elevation_z_m;
  var w = unproj(e.clientX, e.clientY, z);
  if(!w){corr.msg = "correction picks work on the plan (top view)"; corrPanelUI(); return;}
  var centers = opCenters(), hitO = null, hd = 0.45;
  for(var id in centers){
    var c = centers[id], d = Math.hypot(c[0] - w[0], c[1] - w[1]);
    if(d < hd){hd = d; hitO = id;}
  }
  if(hitO){
    var o = S.openings.filter(function(x){return x.id === hitO;})[0] || {};
    corr.selO = hitO; corr.selS = o.space_id || null; corr.msg = "";
    corrPanelUI(); render(); return;
  }
  var hit = null;
  S.spaces.forEach(function(sp){
    if(hit || !visible(sp) || !sp.polygon || sp.polygon.length < 3) return;
    if(inPoly(sp.polygon, w[0], w[1])) hit = sp;
  });
  if(!hit){corr.selS = null; corr.selO = null; corr.arm = null; corr.armPts = [];
    corr.msg = ""; corrPanelUI(); render(); return;}
  if(corr.arm === "merge" && corr.selS && hit.id !== corr.selS){
    corr.ops.push({op: "merge_spaces", id: corr.selS, value: hit.id});
    corr.arm = null; corr.msg = ""; corrPanelUI(); render(); return;
  }
  if(corr.arm === "split" && corr.selS && hit.id !== corr.selS){
    corr.arm = null; corr.armPts = []; corr.selS = hit.id; corr.selO = null;
    corr.msg = ""; corrPanelUI(); render(); return;
  }
  if(corr.arm === "split"){
    corr.armPts.push(snapBoundary(hit.polygon, w[0], w[1]));
    if(corr.armPts.length >= 2){
      corr.ops.push({op: "split_space", id: corr.selS, a: corr.armPts[0], b: corr.armPts[1]});
      corr.arm = null; corr.armPts = [];
    }
    corr.msg = ""; corrPanelUI(); render(); return;
  }
  corr.selS = hit.id; corr.selO = null; corr.msg = ""; corrPanelUI(); render();
}
function corrPanelUI(){
  if(!corr.on){corrPanel.innerHTML = ""; return;}
  var h = "<h2>Correct</h2>";
  if(corr.msg) h += '<div class="c-msg">' + esc(corr.msg) + "</div>";
  if(!corr.selS && !corr.selO && !corr.ops.length && !corr.msg)
    h += '<div class="note">click a room or an opening in the plan to correct it; ops are '
      + 'applied by <b>matchline viewer-apply</b> through the decisions workflow</div>';
  if(corr.selO){
    var o = S.openings.filter(function(x){return x.id === corr.selO;})[0];
    if(o){
      h += '<div class="row">opening <b>' + esc(o.id) + "</b> · " + esc(o.category) + "</div>";
      if(o.category !== "skylight"){
        var to = o.category === "door" ? "window" : "door";
        h += '<div class="row"><button class="c-act" data-op="reclassify_opening" data-id="'
          + esc(o.id) + '" data-val="' + to + '">make ' + to + "</button></div>";
      }
    }
  }
  if(corr.selS && spById[corr.selS]){
    var sp = spById[corr.selS];
    h += '<div class="row">space <b>' + esc(sp.id) + "</b> · " + esc(sp.label) + "</div>";
    h += '<div class="row">name <input id="c-name" value="' + esc(sp.name)
      + '"> number <input id="c-num" size="5" value="' + esc(sp.number) + '"></div>';
    h += '<div class="row"><button class="c-act" data-op="rename_space" data-id="' + esc(sp.id)
      + '">rename</button></div>';
    h += '<div class="row"><button class="c-act" data-arm="merge">'
      + "merge into… (then click the other room)" + "</button></div>";
    h += '<div class="row"><button class="c-act" data-arm="split">'
      + "split… (click two edge points)" + "</button></div>";
    var cs = (S.corrections || []).filter(function(c){return c.target_id === corr.selS;});
    if(cs.length){
      h += '<div class="row c-hist" style="margin-top:6px"><b>corrections</b></div>';
      cs.forEach(function(c){
        h += '<div class="row c-hist">' + esc(c.key) + ": " + esc(c.before) + " → " + esc(c.after)
          + ' <button class="c-act" data-revert="' + esc(c.key) + '">revert</button></div>';
      });
    }
  }
  if(corr.arm === "split") h += '<div class="row c-msg">split: ' + corr.armPts.length
    + "/2 edge points picked" + "</div>";
  if(corr.arm === "merge") h += '<div class="row c-msg">'
    + "merge: now click the room to merge into" + "</div>";
  if(corr.ops.length){
    h += '<div class="row" style="margin-top:6px"><b>'
      + corr.ops.length + " op(s) queued</b></div>";
    corr.ops.forEach(function(op, i){
      h += '<div class="row">' + esc(op.op) + " " + esc(op.id)
        + (op.value !== undefined ? " → " + esc(op.value) : "")
        + ' <button class="c-rm" data-rm="' + i + '">x</button></div>';
    });
    h += '<div class="row"><button id="c-dl">download viewer_corrections.json</button></div>';
  }
  corrPanel.innerHTML = h;
}
corrPanel.onclick = function(e){
  var t = e.target;
  if(t.id === "c-dl"){
    var doc = {schema: "matchline.viewer_corrections/1", model: S.model_name, ops: corr.ops};
    var a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2)],
      {type: "application/json"}));
    a.download = "viewer_corrections.json"; a.click();
    return;
  }
  var v = t.getAttribute ? t.getAttribute("data-rm") : null;
  if(v !== null && v !== ""){corr.ops.splice(parseInt(v, 10), 1); corrPanelUI(); return;}
  v = t.getAttribute ? t.getAttribute("data-revert") : null;
  if(v){corr.ops.push({op: "revert_to_auto", key: v}); corrPanelUI(); return;}
  v = t.getAttribute ? t.getAttribute("data-arm") : null;
  if(v){corr.arm = v; corr.armPts = []; corrPanelUI(); return;}
  if(t.className === "c-act" && t.getAttribute("data-op")){
    var op = t.getAttribute("data-op"), id = t.getAttribute("data-id");
    if(op === "rename_space"){
      var s0 = spById[id];
      var ni = document.getElementById("c-name"), nu = document.getElementById("c-num");
      var name = ni ? String(ni.value).trim() : "", num = nu ? String(nu.value).trim() : "";
      if(s0 && name !== s0.name)
        corr.ops.push({op: "rename_space", id: id, field: "name", value: name});
      if(s0 && num !== s0.number)
        corr.ops.push({op: "rename_space", id: id, field: "number", value: num});
    } else if(op === "reclassify_opening"){
      corr.ops.push({op: "reclassify_opening", id: id, value: t.getAttribute("data-val")});
    }
    corrPanelUI(); return;
  }
};
function drawCorrSel(){
  var s = corr.selS && spById[corr.selS];
  if(s && visible(s) && s.polygon && s.polygon.length > 2){
    drawPoly(s.polygon.map(function(p){return [p[0], p[1], lvl(s.level).elevation_z_m];}),
      null, "#1f4e79", 3);
  }
  if(corr.selO){
    var c = opCenters()[corr.selO];
    var o = S.openings.filter(function(x){return x.id === corr.selO;})[0];
    if(c && o){
      var z = lvl(o.space_level).elevation_z_m;
      drawPoly([[c[0] - 0.3, c[1] - 0.3, z], [c[0] + 0.3, c[1] - 0.3, z],
        [c[0] + 0.3, c[1] + 0.3, z], [c[0] - 0.3, c[1] + 0.3, z]], null, "#c62828", 2);
    }
  }
}
bind(); legend(); setLevel("all"); view(0, 35 * Math.PI / 180, null);
"""


def _js() -> str:
    js = _JS.replace("__HEAT__", json.dumps(list(HEAT)))
    return js.replace("__MEPCOLORS__", json.dumps(MEP_COLORS))


def _scene_json(scene: dict) -> str:
    # "</" never appears inside a script block: escape it for inline safety.
    return json.dumps(scene, default=str).replace("</", r"<\/")


def _script(scene: dict) -> str:
    return _js().replace("__SCENE__", _scene_json(scene))


def fragment(scene: dict, banner: str = "") -> str:
    """The viewer as an embeddable HTML fragment (style + markup + script).

    The export package embeds this in ``share_back.html`` (#999): one render
    core, two homes.
    """
    style = "<style>" + _STYLE + "</style>" if banner == "" else ""
    b = f'<div class="banner">{html.escape(banner)}</div>' if banner else ""
    return style + b + _BODY + "<script>" + _script(scene) + "</script>"


def page_html(scene: dict, title: str = "matchline review viewer") -> str:
    """The viewer as one complete self-contained HTML page (CLI home)."""
    return (
        "<!DOCTYPE html><html><head><meta charset=utf-8><title>"
        + html.escape(title)
        + "</title>"
        + "<style>"
        + _STYLE
        + "html,body{height:100%}#stage{position:absolute;inset:0}"
        + "</style></head><body>"
        + _BODY
        + "<script>"
        + _script(scene)
        + "</script></body></html>"
    )


def _prov_conf(prov) -> Optional[float]:
    return None if prov is None else float(getattr(prov, "confidence", 0.0) or 0.0)


# MEP overlay colors in the viewer canvas: one hue per component type.
MEP_COLORS = {
    "diffuser": "#2e7d32",
    "grille": "#388e3c",
    "vav": "#e65100",
    "ahu": "#c62828",
    "sensor": "#1565c0",
}


def _symbol_row(ref, level_id: str) -> dict:
    """One MEP symbol instance, verbatim from the model component (#1004)."""
    prov = getattr(ref, "provenance", None)
    return {
        "id": ref.id,
        "type": ref.type,
        "tag": ref.tag,
        "x": float(ref.x_m),
        "y": float(ref.y_m),
        "level": level_id,
        "confidence": _prov_conf(prov),
        "sheet": (getattr(prov, "sheet_id", "") or "") if prov is not None else "",
    }


def mep_scene(model, space_level: dict, level_of_space: dict) -> dict:
    """The MEP takeoff and trace overlay section of the scene (#1004).

    Everything here is read straight off the canonical model the pipeline
    wrote: component instances with their detected positions, the zone
    assignments, and the air loops with their zone lists. The viewer never
    re-derives any of it; counts are an aggregation of the instances below,
    not a separate takeoff.
    """
    symbols: list = []
    seen = set()
    for sid, sp in model.spaces.items():
        hv = getattr(sp, "hvac", None)
        if hv is None:
            continue
        lvl = level_of_space.get(sid, "")
        for ref in hv.diffusers + hv.terminal_units + hv.sensors:
            if ref is None:
                continue
            key = (ref.type, ref.id, ref.tag, round(ref.x_m, 6), round(ref.y_m, 6))
            if key in seen:
                continue
            seen.add(key)
            symbols.append(_symbol_row(ref, lvl))
    for zid, z in getattr(model, "zones", {}).items():
        for ref in [z.terminal_unit] + list(z.diffusers) + list(z.sensors):
            if ref is None:
                continue
            key = (ref.type, ref.id, ref.tag, round(ref.x_m, 6), round(ref.y_m, 6))
            if key in seen:
                continue
            seen.add(key)
            symbols.append(_symbol_row(ref, z.level_id))
    counts: dict = {}
    for sym in symbols:
        counts[sym["type"]] = counts.get(sym["type"], 0) + 1
    air_loops = []
    for a in getattr(model, "air_loops", []):
        prov = getattr(a, "provenance", None)
        air_loops.append(
            {
                "tag": a.tag,
                "zones": list(a.zone_ids),
                "confidence": _prov_conf(prov),
                "sheet": (getattr(prov, "sheet_id", "") or "") if prov is not None else "",
            }
        )
    return {"symbols": symbols, "counts": counts, "air_loops": air_loops}


def build_scene(model, corrections_doc: Optional[dict] = None) -> dict:
    """Read the canonical model into the viewer scene.

    Geometry is copied verbatim (polygons, wall endpoints, heights); nothing
    is recomputed, re-projected or simplified here, and no second geometry
    file is written anywhere. ``corrections_doc`` (a ``corrections.json``
    document, #997) lists the still-standing corrections so Correct mode can
    offer revert-to-automatic from the viewer (#1002).
    """
    levels = [
        {
            "id": lv.id,
            "name": lv.name,
            "elevation_z_m": lv.elevation_z_m,
            "wall_height_m": lv.wall_height_m,
        }
        for lv in model.levels
    ]
    zone_of: dict = {}
    for zid, z in getattr(model, "zones", {}).items():
        for sid in z.space_ids:
            zone_of.setdefault(sid, []).append(zid)
    spaces = []
    for sid, sp in model.spaces.items():
        if not sp.polygon_m:
            continue
        lv = next((l for l in levels if l["id"] == sp.level_id), None)
        if lv is None:
            continue
        conf = sp.label_confidence
        ptc = sp.poly_type_confidence
        if conf is not None and ptc is not None:
            conf = min(conf, ptc)
        elif conf is None:
            conf = ptc
        spaces.append(
            {
                "id": sid,
                "level": sp.level_id,
                "label": sp.label,
                "name": sp.name,
                "number": sp.number,
                "polygon": [[float(p[0]), float(p[1])] for p in sp.polygon_m],
                "height_m": sp.height_m,
                "ceiling_height_m": sp.ceiling_height_m,
                "confidence": conf,
                "poly_type": sp.poly_type,
                "zones": zone_of.get(sid, []),
            }
        )
    space_level = {s["id"]: s["level"] for s in spaces}
    level_of_space = {sid: sp.level_id for sid, sp in model.spaces.items()}
    envelope = []
    for w in getattr(model, "envelope", []):
        if not w.from_m or not w.to_m:
            continue
        envelope.append(
            {
                "id": w.id,
                "facade": w.facade,
                "from": [float(w.from_m[0]), float(w.from_m[1])],
                "to": [float(w.to_m[0]), float(w.to_m[1])],
                "height_m": w.height_m,
                "space_id": w.space_id,
                "space_level": space_level.get(w.space_id, ""),
                "confidence": _prov_conf(w.provenance),
            }
        )
    openings = []
    for sid, sp in model.spaces.items():
        if sid not in space_level:
            continue
        for o in sp.openings:
            if o.host_facade not in FACADES or not o.host_interval_m:
                continue
            openings.append(
                {
                    "id": o.id,
                    "space_id": sid,
                    "space_level": space_level[sid],
                    "facade": o.host_facade,
                    "interval": [float(o.host_interval_m[0]), float(o.host_interval_m[1])],
                    "cross": float((o.host_interval_m[2]) if len(o.host_interval_m) > 2 else 0.0),
                    "sill": o.sill_m,
                    "head": o.head_m,
                    "height": o.height_m,
                    "width": o.width_m,
                    "category": o.category,
                    "confidence": _prov_conf(o.provenance),
                }
            )
    zones = []
    for zid, z in getattr(model, "zones", {}).items():
        row = {"id": zid, "spaces": z.space_ids, "air_loop": getattr(z, "air_loop", "") or ""}
        prov = getattr(z, "provenance", None)
        if prov is not None:
            row["confidence"] = _prov_conf(prov)
            row["sheet"] = getattr(prov, "sheet_id", "") or ""
        zones.append(row)
    review_open = sum(1 for i in getattr(model, "review_queue", []) if i.status == "open")
    targets: List[str] = []
    for it in getattr(model, "review_queue", []):
        if it.status != "open":
            continue
        t = it.target or {}
        eid = t.get("id")
        if eid and (eid in space_level or eid in {o["id"] for o in openings}):
            targets.append(eid)
    standing = []
    for c in (corrections_doc or {}).get("corrections", []):
        if c.get("undone"):
            continue
        t = c.get("target") or {}
        standing.append(
            {
                "key": corrections.field_key(t, c.get("field", "")),
                "item_id": c.get("item_id", ""),
                "target_kind": t.get("kind", ""),
                "target_id": t.get("id", ""),
                "field": c.get("field", ""),
                "before": c.get("before"),
                "after": c.get("after"),
            }
        )
    return {
        "schema": SCHEMA,
        "model_name": model.name,
        "levels": levels,
        "spaces": spaces,
        "envelope": envelope,
        "openings": openings,
        "zones": zones,
        "mep": mep_scene(model, space_level, level_of_space),
        "review_open": review_open,
        "review_targets": targets,
        "corrections": standing,
    }
