<?xml version="1.0" encoding="UTF-8"?>
<!--
  matchline repo policy for BuildingSync output (issue #1293).

  This ruleset is NOT an upstream ASHRAE 211 artifact: it encodes matchline's
  own business rules for the files bem/buildingsync_export.py writes. It runs
  through the same engine as the upstream L000 use case (lxml.isoschematron,
  XSLT1 binding), so both gates report failures in the same shape.

  Every rule is written against the element and attribute names the writer
  actually emits (read bem/buildingsync_export.py before changing a rule):

    * zone-load inputs:  ThermalZone/Spaces/Space/FloorAreas/FloorArea/FloorAreaValue
                         ThermalZone/UserDefinedFields/.../matchline:zone_flow_per_area_m3s_per_m2
    * HVAC sizing:        HVACSystem/UserDefinedFields/.../matchline:cooling_total_btuh
                                        matchline:cooling_sensible_btuh
                                        matchline:heating_btuh
    * zone -> system:     ThermalZone/DeliveryIDs/DeliveryID/@IDref -> Delivery/@ID
                         (the Delivery hangs under HVACSystem/HeatingAndCoolingSystems/Deliveries)
    * fan -> system:      FanSystem/LinkedSystemIDs/LinkedSystemID/@IDref -> HVACSystem/@ID
    * envelope values:    WallSystem/RoofSystem/FoundationSystem/FenestrationSystem
                         UserDefinedFields/.../matchline:*_u_value_w_m2k
    * envelope source:    the construction id travels in the element's @ID
                         (FenestrationSystemType-<construction id>), where the id is
                         either drawing-derived or an ASHRAE 90.1-2019 Table 5.5
                         library id (t55-*).

  Rule ids (ML-POL-*) are quoted verbatim in the failure text so a report
  names the rule that was broken, not just "validation failed".
-->
<sch:schema xmlns:sch="http://purl.oclc.org/dsdl/schematron" schemaVersion="1.0.0" queryBinding="xslt1">
  <sch:title>matchline repo policy for BuildingSync output (#1293)</sch:title>
  <sch:ns prefix="auc" uri="http://buildingsync.net/schemas/bedes-auc/2019"/>
  <sch:pattern id="matchline_zone_load_inputs">
    <sch:title>Zone-load-impacting inputs precede and are traceable to HVAC sizing</sch:title>

    <sch:rule context="//auc:HVACSystem[auc:UserDefinedFields/auc:UserDefinedField[auc:FieldName='matchline:cooling_total_btuh' or auc:FieldName='matchline:cooling_sensible_btuh' or auc:FieldName='matchline:heating_btuh']]">
      <sch:assert test="count(preceding::auc:ThermalZone[(auc:UserDefinedFields/auc:UserDefinedField[auc:FieldName='matchline:zone_flow_per_area_m3s_per_m2'] or auc:Spaces/auc:Space/auc:FloorAreas/auc:FloorArea/auc:FloorAreaValue) and auc:DeliveryIDs/auc:DeliveryID/@IDref = current()//auc:Delivery/@ID]) &gt; 0" role="ERROR">ML-POL-001: HVAC system <sch:value-of select="current()/@ID"/> states a sizing capacity (matchline:cooling_total_btuh / matchline:cooling_sensible_btuh / matchline:heating_btuh) but no earlier thermal zone that carries a zone-load-impacting input (a Space FloorAreaValue or matchline:zone_flow_per_area_m3s_per_m2) is linked to it through DeliveryID. Zone loads must be present, and traceable to the sized system, before the sizing values.</sch:assert>
    </sch:rule>

    <sch:rule context="//auc:FanSystem[auc:UserDefinedFields/auc:UserDefinedField[auc:FieldName='matchline:fan_airflow_lps']]">
      <sch:assert test="count(//auc:HVACSystem[@ID = current()//auc:LinkedSystemID/@IDref and (auc:UserDefinedFields/auc:UserDefinedField[auc:FieldName='matchline:cooling_total_btuh' or auc:FieldName='matchline:cooling_sensible_btuh' or auc:FieldName='matchline:heating_btuh'])]) &gt; 0" role="ERROR">ML-POL-002: fan system <sch:value-of select="current()/@ID"/> states an airflow (matchline:fan_airflow_lps) but is not linked through LinkedSystemID to an HVAC system that states a sizing capacity (matchline:cooling_total_btuh / matchline:heating_btuh). The airflow must be traceable to the sized system it serves.</sch:assert>
    </sch:rule>

  </sch:pattern>

  <sch:pattern id="matchline_envelope_traceability">
    <sch:title>Envelope thermal values are traceable to drawing data or ASHRAE 90.1-2019 Table 5.5</sch:title>

    <sch:rule context="//auc:FenestrationSystem[auc:UserDefinedFields/auc:UserDefinedField[auc:FieldName='matchline:u_value_w_m2k']]">
      <sch:assert test="contains(current()/@ID, 'FenestrationSystemType-') and string-length(substring-after(current()/@ID, 'FenestrationSystemType-')) &gt; 0 and not(number(substring-after(current()/@ID, 'FenestrationSystemType-')))" role="ERROR">ML-POL-003: fenestration system <sch:value-of select="current()/@ID"/> states a thermal value (matchline:u_value_w_m2k) but does not name its source construction. A fenestration thermal value must be traceable to a drawing-derived construction id or an ASHRAE 90.1-2019 Table 5.5 library construction (t55-*); expected an ID of the form FenestrationSystemType-&lt;construction id&gt;.</sch:assert>
    </sch:rule>

    <sch:rule context="//auc:WallSystems/auc:WallSystem | //auc:RoofSystems/auc:RoofSystem | //auc:FoundationSystems/auc:FoundationSystem | //auc:FenestrationSystems/auc:FenestrationSystem">
      <sch:assert test="count(auc:UserDefinedFields/auc:UserDefinedField[auc:FieldName[contains(., 'u_value_w_m2k')]]) &gt; 0" role="ERROR">ML-POL-004: envelope element <sch:value-of select="current()/@ID"/> carries no thermal value (no matchline:*_u_value_w_m2k user-defined field). An envelope element must state the thermal value it stands for, in the unit-bearing matchline form, so the value can be traced back to the drawing data or to ASHRAE 90.1-2019 Table 5.5.</sch:assert>
    </sch:rule>

  </sch:pattern>
</sch:schema>
