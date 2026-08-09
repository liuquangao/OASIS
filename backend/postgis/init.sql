CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS postgis_raster;

CREATE SCHEMA IF NOT EXISTS terrain;
CREATE SCHEMA IF NOT EXISTS land_surface;
CREATE SCHEMA IF NOT EXISTS hydrology;
CREATE SCHEMA IF NOT EXISTS flood_information;
CREATE SCHEMA IF NOT EXISTS built_environment;
CREATE SCHEMA IF NOT EXISTS social_vulnerability;

CREATE SCHEMA IF NOT EXISTS flood_hazard;
CREATE SCHEMA IF NOT EXISTS exposure;
CREATE SCHEMA IF NOT EXISTS vulnerability;
CREATE SCHEMA IF NOT EXISTS environmental_surface;

COMMENT ON DATABASE glasgow_flood IS 'Static spatial database for Glasgow flood risk WebGIS MVP.';
