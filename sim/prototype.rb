# Build the DOE prototype that sim/reference_eui.json is measured on (#752).
#
#   openstudio sim/prototype.rb OUT_DIR BUILDING_TYPE TEMPLATE CLIMATE_ZONE EPW_NAME
#
# openstudio-standards' own prototype generator; EPW_NAME is the weather
# file name it looks up in its bundled weather data. Writes OUT_DIR/prototype.osm.
require 'openstudio'
require 'openstudio-standards'

out, btype, template, cz, epw = ARGV
cz = "ASHRAE 169-2013-#{cz}" unless cz.start_with?('ASHRAE')
std = Standard.build("#{template}_#{btype}")
model = OpenStudio::Model::Model.new
std.model_create_prototype_model(cz, epw, "#{out}/sizing", false, model)
abort('prototype has no floor area') unless model.getBuilding.floorArea > 0
puts "prototype_area_m2=#{model.getBuilding.floorArea}"
model.save(OpenStudio::Path.new("#{out}/prototype.osm"), true)
