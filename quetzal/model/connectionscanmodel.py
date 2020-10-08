
from quetzal.model import stepmodel
from quetzal.engine import csa 
import pandas as pd
import networkx as nx
import numpy as np
from shapely import geometry
from syspy.spatial import spatial
import bisect
import warnings
from functools import wraps
import shutil
import ntpath
import uuid
from tqdm import tqdm

def read_hdf(filepath, *args, **kwargs):
    m = ConnectionScanModel(hdf_database=filepath, *args, **kwargs)
    return m

def read_zip(filepath, *args, **kwargs):
    try:
        m = ConnectionScanModel(zip_database=filepath, *args, **kwargs)
        return m
    except : 
        # the zip is a zipped hdf and can not be decompressed
        return read_zipped_hdf(filepath, *args, **kwargs)

def read_zipped_hdf(filepath, *args, **kwargs):
    filedir = ntpath.dirname(filepath)
    tempdir = filedir + '/quetzal_temp' + '-' + str(uuid.uuid4())
    shutil.unpack_archive(filepath, tempdir)
    m = read_hdf(tempdir + r'/model.hdf', *args, **kwargs)
    shutil.rmtree(tempdir)
    return m


def read_json(folder):
    m = TimeExpandedModel(json_folder=folder)
    return m

class ConnectionScanModel(stepmodel.StepModel):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not 'time_interval' in dir(self):
            self.time_interval = [0, 24*3600-1]

    def preparation_build_connection_dataframe(self, min_transfer_time=0):

        links = self.links.loc[self.links['a'] != self.links['b']]

        try:
            links = pd.merge(
                links, self.nodes[['transfer_duration']], 
                how='left', left_on='a', 
                right_index=True
            )
            links['min_transfer_time'] = links['transfer_duration'].fillna(0) + min_transfer_time
        except KeyError:
            links['min_transfer_time'] = min_transfer_time

        pseudo_links = links[
            ['a', 'b', 'departure_time', 'arrival_time','min_transfer_time', 'trip_id']
        ].copy()
        pseudo_links['link_index'] = pseudo_links.index
        pseudo_links['departure_time'] -= links['min_transfer_time']
        zone_to_transit = csa.time_zone_to_transit(pseudo_links, self.zone_to_transit)
        footpaths = csa.time_footpaths(pseudo_links, self.footpaths)
        
        pseudo_connections = pd.concat([pseudo_links, footpaths, zone_to_transit])
        pseudo_connections = pseudo_connections[pseudo_connections['a'] != pseudo_connections['b']]

        # connections of each trip are consecutive
        pseudo_connections.sort_values(by=['trip_id', 'departure_time'], inplace=True)
        pseudo_connections['csa_index'] = range(len(pseudo_connections)) # use int as index
        pseudo_connections.sort_values('departure_time', ascending=False, inplace=True)
        
        self.pseudo_connections = pseudo_connections

    def step_pt_pathfinder(self, min_transfer_time=0, time_interval=None):
        
        time_interval = time_interval if time_interval is not None else self.time_interval
        
        self.preparation_build_connection_dataframe(min_transfer_time=min_transfer_time)
        pseudo_connections = self.pseudo_connections

        # DROP EGRESS
        egress = pseudo_connections.set_index('direction').loc['egress']
        egress_by_zone = egress.groupby(['b'])['csa_index'].agg(set).to_dict()
        drop_egress = {}
        zone_set = set(self.zones.index)
        for zone in zone_set:
            drop_egress[zone] = set(egress['csa_index']) - egress_by_zone[zone]
            # for each zone, which egress links are not useful 
            # we want to drop all of them but the ones leading to the zone

        # TIME FILTER
        # all links reaching b
        egress_time_dict = egress.groupby('b')['departure_time'].agg(list).to_dict()
        departure_times = list(pseudo_connections['departure_time'])[::-1]
        
        zone_set = set(self.zones.index)
        stop_set = set(pseudo_connections['a']).union(set(pseudo_connections['b']))
        Ttrip_inf =  {t: float('inf') for t in set(pseudo_connections['trip_id'])}
        columns = ['a', 'b', 'departure_time', 'arrival_time', 'csa_index', 'trip_id']
        decreasing_departure_connections = pseudo_connections[columns].to_dict(orient='record')

        pareto = []
        for target in tqdm(zone_set):

            # BUILD CONNECTIONS
            start, end = time_interval
            slice_end = bisect.bisect_left(departure_times, time_interval[0])
            end = max([t for t in egress_time_dict[target] if t <= end] + [0]) 
            slice_start = bisect.bisect_right(departure_times, end)
            time_interval_connections = decreasing_departure_connections[-slice_start: -slice_end]
            forbidden = drop_egress[target]
            connections = [c for c in time_interval_connections if c['csa_index'] not in forbidden]

            profile, predecessor = csa.csa_profile(
                connections, target=target,
                stop_set=stop_set, Ttrip=Ttrip_inf.copy()
            )

            for source, source_profile in profile.items():
                if source not in zone_set:
                    continue
                for departure, arrival, c in source_profile:
                    path = csa.get_path(predecessor, c) 
                    path = [source] + path + [target]
                    pareto.append((source, target, departure, arrival, c, path))

        df = pd.DataFrame(
            pareto, 
            columns=['source', 'target', 
            'departure_time', 'arrival_time', 'last_connection', 'path']
        )
        clean = pseudo_connections[['csa_index','trip_id']].dropna()
        trip_connections = clean.sort_values(by='csa_index').groupby('trip_id')['csa_index'].apply(list).to_dict()
        connection_trip =  clean.set_index('csa_index')['trip_id'].to_dict()
        values = [
            csa.path_to_boarding_links_and_boarding_path(
                path,
                trip_connections=trip_connections, 
                connection_trip=connection_trip
            ) for path in df['path']
        ]
        df['connection_path'] = [v[0] for v in values]
        df['first_connections'] = [v[1] for v in values]
        pool = pseudo_connections[['link_index', 'csa_index']].dropna()
        d = pool.set_index('csa_index')['link_index'].to_dict()
        df['link_path'] = [
            [d[i] for i in p if i in d]
            for p in df['connection_path']
        ]

        df['boarding_links'] = [
            [d[i] for i in p if i in d] 
            for p in df['first_connections']
        ]

        self.los = df