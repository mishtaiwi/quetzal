import json
import os

import geopandas as gpd

default_crs_string = "urn:ogc:def:crs:EPSG::2154"
default_prj_file = "../syspy_utils/gis_resources/epsg2154.prj"


def set_geojson_crs(file, crs_string=default_crs_string, encoding='utf-8'):
    with open(file, 'r', encoding=encoding) as infile:
        data = json.load(infile)
        infile.close()
    with open(file, 'w', encoding=encoding) as outfile:
        data['crs'] = {"type": "name", "properties": {"name": crs_string}}
        json.dump(data, outfile)


def set_shp_crs(file, prj_file=default_prj_file):
    file_without_extension = file.split('.shp')[0]
    copyfile(prj_file, file_without_extension + r'.prj')


def gdf_to_geojson(gdf, filename, crs_string=default_crs_string, encoding='utf-8'):
    try:
        os.remove(filename)
    except OSError:
        pass
    gdf.to_file(filename, driver='GeoJSON', encoding=encoding)
    set_geojson_crs(filename, crs_string, encoding=encoding)
