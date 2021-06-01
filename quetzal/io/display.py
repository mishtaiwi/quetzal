import folium
import pandas as pd
import shapely
from syspy.spatial import geometries, spatial
from tqdm import tqdm


def longlat(coords):
    return [coords[1], coords[0]]


def path(self, origin, destination, public=True, private=False, *args, **kwargs):
    if public:
        m = pt_path(self, origin, destination, *args, **kwargs)
        if private:
            m = car_path(self, origin, destination, m=m, *args, **kwargs)
    elif private:
        m = car_path(self, origin, destination, *args, **kwargs)
    return m


def car_path(self, origin, destination, m=None):
    ntlegs = self.zone_to_road

    first = self.car_los.groupby(['origin', 'destination'], as_index=False).first()
    row = first.set_index(['origin', 'destination']).loc[(origin, destination)]
    o = self.centroids['geometry'].loc[origin]
    d = self.centroids['geometry'].loc[destination]
    location = longlat(shapely.geometry.MultiPoint([o, d]).centroid.coords[0])
    if m is None:
        m = folium.Map(location=location, zoom_start=13)

    path_links = self.road_links.loc[row['link_path']]
    polylines = path_links['geometry']
    polyline = geometries.line_list_to_polyline(list(polylines))
    name = 'all'
    coordinates = [longlat(coords) for coords in list(polyline.coords)]
    my_PolyLine = folium.PolyLine(locations=coordinates, weight=5, popup=str(name))
    m.add_children(my_PolyLine)

    for name, node, color in [(origin, o, 'green'), (destination, d, 'red')]:
        m.add_children(
            folium.CircleMarker(
                longlat(list(node.coords)[0]),
                fill=True,
                color=color,
                fill_opacity=1,
                radius=5,
                popup=str(name)
            )
        )
    return m


def all_car_paths(self, origin, destination, m=None):
    ntlegs = self.zone_to_road

    o = self.centroids['geometry'].loc[origin]
    d = self.centroids['geometry'].loc[destination]
    location = longlat(shapely.geometry.MultiPoint([o, d]).centroid.coords[0])

    od_paths = self.car_los.set_index(
        ['origin', 'destination']
    ).sort_values('time').loc[(origin, destination)]

    polyline_list = []
    i = 0
    for name, row in tqdm(list(od_paths.iterrows())):
        path_links = self.road_links.loc[row['link_path']]
        polylines = path_links['geometry']
        polyline = geometries.line_list_to_polyline(list(polylines))
        try:
            weight = row['weight']
        except KeyError:
            weight = 1
        coordinates = [longlat(coords) for coords in list(polyline.coords)]
        my_PolyLine = folium.PolyLine(
            locations=coordinates,
            weight=weight,
            popup=str(weight),
            opacity=1
        )
        i += 1
        polyline_list.append(my_PolyLine)

    if m is None:
        m = folium.Map(location=location, zoom_start=13)

    for my_PolyLine in polyline_list:
        m.add_children(my_PolyLine)

    for name, node, color in [(origin, o, 'green'), (destination, d, 'red')]:
        m.add_children(
            folium.CircleMarker(
                longlat(list(node.coords)[0]),
                fill=True,
                color=color,
                fill_opacity=1,
                radius=5,
                popup=str(name)
            )
        )
    return m


def pt_path(self, origin, destination, m=None, color_column=None, group_name='trip_id'):
    if self.walk_on_road:
        footpaths = self.road_links.copy()
        ntlegs = self.zone_to_road
    else:
        footpaths = self.footpaths
        ntlegs = self.zone_to_transit

    first = self.pt_los.groupby(['origin', 'destination'], as_index=False).first()
    row = first.set_index(['origin', 'destination']).loc[(origin, destination)]

    o = self.centroids['geometry'].loc[origin]
    d = self.centroids['geometry'].loc[destination]
    location = longlat(shapely.geometry.MultiPoint([o, d]).centroid.coords[0])
    if m is None:
        m = folium.Map(location=location, zoom_start=13)

    path_links = self.links.loc[row['link_path']].copy()
    path_links['color'] = 'blue' if color_column is None else path_links[color_column]
    polylines = path_links.groupby(
        [group_name, 'color']
    )['geometry'].agg(geometries.line_list_to_polyline)
    for (name, color), polyline in polylines.to_dict().items():
        coordinates = [longlat(coords) for coords in list(polyline.coords)]
        my_PolyLine = folium.PolyLine(locations=coordinates, weight=5, color=color, popup=name)
        m.add_children(my_PolyLine)

    polylines = footpaths.set_index(['a', 'b']).loc[row['footpaths']]['geometry']
    for polyline in polylines:
        coordinates = [longlat(coords) for coords in list(polyline.coords)]
        my_PolyLine = folium.PolyLine(locations=coordinates, weight=3, color='gray')
        m.add_children(my_PolyLine)

    polylines = ntlegs.set_index(['a', 'b']).loc[row['ntlegs']]['geometry']
    for polyline in polylines:
        coordinates = [longlat(coords) for coords in list(polyline.coords)]
        my_PolyLine = folium.PolyLine(locations=coordinates, weight=3, color='black')
        m.add_children(my_PolyLine)

    transit_node_path = [
        n for n in row['node_path']
        if n in set(row['boardings']).union(set(row['alightings']))
    ]
    nodes = self.nodes.loc[transit_node_path]['geometry']
    for name, node in nodes.to_dict().items():
        m.add_children(
            folium.CircleMarker(
                longlat(list(node.coords)[0]),
                fill=True,
                color='gray',
                fill_opacity=1,
                radius=3,
                popup=name
            )
        )
    for name, node, color in [(origin, o, 'green'), (destination, d, 'red')]:
        m.add_children(
            folium.CircleMarker(
                longlat(list(node.coords)[0]),
                fill=True,
                color=color,
                fill_opacity=1,
                radius=5,
                popup=str(name)
            )
        )
    return m


def all_pt_paths(
    self,
    origin,
    destination,
    m=None,
    verbose=True,
    color_column=None,
    group_name='trip_id'
):
    od_paths = self.pt_los.set_index(
        ['origin', 'destination']
    ).sort_values('gtime').loc[origin].loc[destination]

    if verbose:
        print(len(od_paths), 'paths')

    i = 0
    for name, row in tqdm(list(od_paths.iterrows())):
        m = one_pt_path(
            self,
            row,
            m=None,
            color_column=color_column,
            group_name=group_name
        ) if i == 0 else one_pt_path(
            self,
            row,
            m=m,
            color_column=color_column,
            group_name=group_name
        )
        i += 1
    return m


def one_pt_path(self, row, m=None, color_column=None, group_name='trip_id'):

    def add_polylines_to_map(m, polylines, **kwargs):
        for polyline in polylines:
            try:
                coordinates = [longlat(coords) for coords in list(polyline.coords)]
                my_PolyLine = folium.PolyLine(locations=coordinates, **kwargs)
                m.add_children(my_PolyLine)
            except AttributeError:
                pass

    row_path = row['path']
    origin = row_path[0]
    destination = row_path[-1]

    o = self.centroids['geometry'].loc[origin]
    d = self.centroids['geometry'].loc[destination]

    location = longlat(shapely.geometry.MultiPoint([o, d]).centroid.coords[0])
    if m is None:
        m = folium.Map(location=location, zoom_start=13)

    path_links = self.links.loc[row['link_path']].copy()
    path_links['color'] = 'blue' if color_column is None else path_links[color_column]
    # Plot links
    polylines = path_links.groupby(
        [group_name, 'color']
    )['geometry'].agg(geometries.line_list_to_polyline)
    for (name, color), polyline in polylines.to_dict().items():
        coordinates = [longlat(coords) for coords in list(polyline.coords)]
        my_PolyLine = folium.PolyLine(locations=coordinates, weight=5, color=color, popup=name)
        m.add_children(my_PolyLine)

    # Plot footpaths
    # PT footpaths-- black
    if len(self.footpaths) > 1:
        polylines = self.footpaths.set_index(['a', 'b']).loc[row['footpaths']]['geometry']
        add_polylines_to_map(m, polylines, weight=4, color='black')

    # Road_to_transit - gray
    if len(self.road_to_transit) > 1:
        polylines = self.road_to_transit.set_index(['a', 'b']).loc[row['footpaths']]['geometry']
        add_polylines_to_map(m, polylines, weight=4, color='gray')

    # Road_links - gray
    if len(self.road_links) > 1:
        polylines = self.road_links.set_index(['a', 'b']).loc[row['footpaths']]['geometry']
        add_polylines_to_map(m, polylines, weight=2, color='gray')

    # Plot ntlegs
    # zone_to_road - gray - dashed
    if len(self.zone_to_road) > 1:
        polylines = self.zone_to_road.set_index(['a', 'b']).loc[row['ntlegs']]['geometry']
        add_polylines_to_map(m, polylines, weight=3, color='gray', dash_array=('5, 5'))

    # zone_to_transit - black - dashed
    if len(self.zone_to_transit) > 1:
        polylines = self.zone_to_transit.set_index(['a', 'b']).loc[row['ntlegs']]['geometry']
        add_polylines_to_map(m, polylines, weight=3, color='black', dash_array=('5, 5'))

    transit_node_path = [
        n for n in row['node_path']
        if n in set(row['boardings']).union(set(row['alightings']))
    ]
    nodes = self.nodes.loc[transit_node_path]['geometry']
    for name, node in nodes.to_dict().items():
        m.add_children(
            folium.CircleMarker(
                longlat(list(node.coords)[0]),
                fill=True,
                color='gray',
                fill_opacity=1,
                radius=3,
                popup=name
            )
        )
    for name, node, color in [(origin, o, 'green'), (destination, d, 'red')]:
        m.add_children(
            folium.CircleMarker(
                longlat(list(node.coords)[0]),
                fill=True,
                color=color,
                fill_opacity=1,
                radius=5,
                popup=str(name)
            )
        )
    return m
