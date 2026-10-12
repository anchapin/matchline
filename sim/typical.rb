# Turn an exported gbXML into a simulation-ready OSM (#752).
#
#   openstudio sim/typical.rb MODEL.xml WEATHER.epw OUT_DIR BUILDING_TYPE TEMPLATE CLIMATE_ZONE
#
# Loads the gbXML, sets the TMY weather and its design days (the .ddy next to
# the .epw; openstudio-standards also needs the .stat there), then lets openstudio-standards add the DOE prototype space types,
# constructions for surfaces that have none, loads, and the inferred HVAC
# template, with a sizing run. Writes OUT_DIR/typical.osm.
require 'openstudio'
require 'openstudio-standards'

gbxml, epw, out, btype, template, cz = ARGV
cz = "ASHRAE 169-2013-#{cz}" unless cz.start_with?('ASHRAE')
loaded = OpenStudio::GbXML::GbXMLReverseTranslator.new.loadModel(OpenStudio::Path.new(gbxml))
abort('gbXML did not load') unless loaded.is_initialized
model = loaded.get
model.getBuilding.setStandardsBuildingType(btype)
ok = OpenstudioStandards::Weather.model_set_weather_file_and_design_days(
  model, weather_file_path: epw,
  ddy_list: [/Htg 99.6. Condns DB/, /Clg .4. Condns DB=>MWB/]
)
abort('weather/design days not set') unless ok
OpenstudioStandards::Weather.model_set_climate_zone(model, cz)
ok = OpenstudioStandards::CreateTypical.create_space_types_and_constructions(model, btype, template, cz)
abort('space types/constructions not created') unless ok
# a space the space-type step left without one gets the whole-building type
st = model.getSpaceTypes.find { |s| s.standardsSpaceType.to_s =~ /WholeBuilding/ } || model.getSpaceTypes.first
model.getSpaces.each { |s| s.setSpaceType(st) unless s.spaceType.is_initialized }
# Infiltration (#768): the DOE prototype rate per above-grade exterior wall
# area, Idesign = 0.2016 cfm/ft2 (0.001024 m3/s-m2), PNNL-18898 (Gowri et al.
# 2009) from 1.8 cfm/ft2 at 75 Pa. openstudio-standards applies its rate per
# exterior surface area, roof included, which over-counts a one-storey
# flat-roof building. Anything that changes a zone load has to be in place
# before the HVAC sizing run, so the rate is set inside the infiltration step
# of create_typical_building_from_model (wrapped below), which runs before
# HVAC is added and sized; its schedules and coefficients are left as created.
INFIL_M3_S_PER_M2_WALL = 0.001024
module OpenstudioStandards
  module Infiltration
    class << self
      alias_method :matchline_nist_infiltration, :model_set_nist_infiltration
      def model_set_nist_infiltration(model, **kwargs)
        ok = matchline_nist_infiltration(model, **kwargs)
        if ok
          model.getSpaceInfiltrationDesignFlowRates.each do |inf|
            inf.setFlowperExteriorWallArea(INFIL_M3_S_PER_M2_WALL)
          end
        end
        ok
      end
    end
  end
end

ok = OpenstudioStandards::CreateTypical.create_typical_building_from_model(
  model, template, climate_zone: cz, hvac_system_type: 'Inferred',
  add_swh: false, add_elevators: false, add_exterior_lights: false,
  add_refrigeration: false, sizing_run_directory: "#{out}/sizing"
)
abort('create_typical_building_from_model failed') unless ok
bad = model.getSpaceInfiltrationDesignFlowRates.reject do |inf|
  inf.designFlowRateCalculationMethod == 'Flow/ExteriorWallArea' &&
    (inf.flowperExteriorWallArea.get - INFIL_M3_S_PER_M2_WALL).abs < 1e-9
end
abort("infiltration rate not applied: #{bad.map { |i| i.name.get }.join(', ')}") unless bad.empty?
puts "zones=#{model.getThermalZones.size} airloops=#{model.getAirLoopHVACs.size} " \
     "conditioned_area_m2=#{model.getBuilding.conditionedFloorArea}"
model.save(OpenStudio::Path.new("#{out}/typical.osm"), true)
