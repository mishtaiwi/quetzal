# -*- coding: utf-8 -*-

from quetzal.analysis import analysis
from quetzal.engine import engine, linearsolver_utils
from quetzal.model import model, transportmodel, summarymodel
from quetzal.io import export
from syspy.syspy_utils import neighbors
import numpy as np
import networkx as nx
import pandas as pd
from tqdm import tqdm
import geopandas as gpd
from syspy.spatial import spatial, geometries
from quetzal.engine import nested_logit

def read_hdf(filepath):
    m = AnalysisModel()
    m.read_hdf(filepath)
    return m


def read_json(folder):
    m = AnalysisModel()
    m.read_json(folder)
    return m


track_args = model.track_args
log = model.log

class AnalysisModel(summarymodel.SummaryModel):



    def _aggregate(self, nb_clusters, cluster_column=None, volume_column='volume'):
        """
        Aggregates a model (in order to perform optimization)
            * requires: nb_clusters, cluster_series, od_stack, indicator
            * builds: cluster_series, aggregated model, reduced indicator
        """
        self.agg = self.copy()
        self.agg.preparation_clusterize_zones(
            nb_clusters,cluster_column, is_od_stack=True,
            volume_columns=[volume_column], volume_od_columns=[volume_column]
        )
        self.cluster_series = self.agg.cluster_series
        self.agg.indicator = linearsolver_utils.reduce_indicator(
            self.indicator,
            self.cluster_series,
            self.od_stack,
            volume_column=volume_column
        )

    def _disaggregate(self):
        self.pivot_stack_matrix, self.od_stack = linearsolver_utils.extrapolate(
                self.agg.pivot_stack_matrix,
                self.od_stack,
                self.cluster_series
        )

    def _build_pivot_stack_matrix(self, constrained_links, linprog_kwargs, **kwargs):
        """
        Builds the pivot_stack_matrix. Performs the optimization.
            * requires: constrained_links, od_stack, indicator
            * builds: pivot_stack_matrix
        """
        self.pivot_stack_matrix = linearsolver_utils.linearsolver(
            self.indicator,
            constrained_links,
            self.od_stack,
            **linprog_kwargs,
            **kwargs
        )

    def _analysis_road_link_path(self, include_road_footpaths=False): 
        """
        Build road_link_path column of pt_los based on link_path
        """
        try:
            link_to_road_links = self.links['road_link_list'].to_dict()
        except KeyError as e:
            raise KeyError('road_link_list column missing: links must be networkasted.')
            
        self.pt_los['road_link_path'] = self.pt_los['link_path'].apply(
            lambda x: [i for l in map(link_to_road_links.get, x) if l is not None for i in l ]
        )
        
        if include_road_footpaths:
            # Footpath to road_link_path
            road_links_dict = self.road_links.reset_index().set_index(['a','b'])[self.road_links.index.name].to_dict()
            
            nan_loc = self.pt_los['footpaths'].isnull()
            self.pt_los.loc[nan_loc, 'footpaths'] = [[]] * nan_loc.sum()
            
            self.pt_los['road_link_path'] += self.pt_los['footpaths'].apply(
                lambda x: [a for a in list(map(lambda l: road_links_dict.get(l), x)) if a is not None]
            )

    def analysis_linear_solver(
        self,
        constrained_links,
        nb_clusters=20,
        cluster_column=None,
        link_path_column='link_path',
        linprog_kwargs={
            'bounds_A': [0.75, 1.5],
            'bounds_emissions': [0.8, 1.2],
            'bounds_tot_emissions': [0.95, 1.05],
            'pas_distance': 200,
            'maxiter': 3000,
            'tolerance': 1e-5
        },
        **kwargs,
        ):
        """
        To perform the optimization on a model object once it is built and run,
        in order to match the observed volumes.
            * requires: od_stack, constrained_links
            * builds: aggregated model, pivot_stack_matrix
        Le but de linear_solver est de modifier la matrice des volumes par OD
        en la multipliant par un pivot, afin de coller aux observations
        recueillies sur certains nœuds/liens du réseau.
        Etapes:
        0. Construction de l'indicatrice (matrice qui indique la présence des
            liens contraints dans chaque OD)
        1. Agrégation du modèle.
        2. Résolution du problème d'optimisation linéaire pour construire
            pivot_stack_matrix (matrice pivot). Plus de détails dans
            linearsolver_utils.
        3. Désagrégation de la matrice pivot pour revenir au modèle de base.
        """
        self.indicator = linearsolver_utils.build_indicator(
            self.od_stack,
            constrained_links,
            link_path_column=link_path_column
            )
        if len(self.zones) < nb_clusters:
            self._build_pivot_stack_matrix(constrained_links, linprog_kwargs, **kwargs)
        else:
            self._aggregate(nb_clusters, cluster_column, **kwargs)
            self.agg._build_pivot_stack_matrix(constrained_links, linprog_kwargs, **kwargs)
            self._disaggregate()

    def analysis_pt_route_type(self, hierarchy):
        route_type_dict = self.links['route_type'].to_dict()
        self.pt_los['route_types'] = self.pt_los['link_path'].apply(
            lambda p: tuple({route_type_dict[l] for l in p})
        )

        def higher_route_type(route_types):
            for mode in hierarchy:
                if mode in route_types:
                    return mode
            return hierarchy[-1]

        self.pt_los['route_type'] = self.pt_los['route_types'].apply(higher_route_type)

    def analysis_car_route_type(self):
        self.car_los['route_types'] = [tuple(['car']) for i in self.car_los.index]
        self.car_los['route_type'] = 'car'


    def analysis_pt_time(self, boarding_time=0, walk_on_road=False):
        footpaths = self.footpaths
        access = self.zone_to_transit

        if walk_on_road:
            road_links = self.road_links.copy()
            road_links['time'] = road_links['walk_time']
            road_to_transit = self.road_to_transit.copy()
            road_to_transit['length'] = road_to_transit['distance']
            footpaths = pd.concat([road_links, road_to_transit, self.footpaths])
            access = pd.concat([self.zone_to_road, self.zone_to_transit])

        d = access.set_index(['a', 'b'])['time'].to_dict()
        self.pt_los['access_time'] = self.pt_los['ntlegs'].apply(
            lambda l: sum([d[t] for t in l]))

        d = footpaths.set_index(['a', 'b'])['time'].to_dict()
        self.pt_los['footpath_time'] = self.pt_los['footpaths'].apply(
            lambda l: sum([d[t] for t in l]))

        d = self.links['time'].to_dict()
        self.pt_los['in_vehicle_time'] = self.pt_los['link_path'].apply(
            lambda l: sum([d[t] for t in l]))
        d = self.links['headway'].to_dict()
        self.pt_los['waiting_time'] = self.pt_los['boarding_links'].apply(
            lambda l: sum([d[t] / 2 for t in l]))
        self.pt_los['boarding_time'] = self.pt_los['boarding_links'].apply(
            lambda t: len(t)*boarding_time)
        self.pt_los['time'] = self.pt_los[
            ['access_time', 'footpath_time', 'waiting_time', 'boarding_time', 'in_vehicle_time']
        ].T.sum()

    def analysis_pt_length(self, walk_on_road=False):
        footpaths = self.footpaths
        access = self.zone_to_transit

        if walk_on_road:
            road_links = self.road_links.copy()
            road_links['time'] = road_links['walk_time']
            road_to_transit = self.road_to_transit.copy()
            road_to_transit['length'] = road_to_transit['distance']
            footpaths = pd.concat([road_links, road_to_transit, self.footpaths])
            access = pd.concat([self.zone_to_road, self.zone_to_transit])

        d = access.set_index(['a', 'b'])['distance'].to_dict()
        self.pt_los['access_length'] = self.pt_los['ntlegs'].apply(
            lambda l: sum([d[t] for t in l]))
        d = footpaths.set_index(['a', 'b'])['length'].to_dict()
        self.pt_los['footpath_length'] = self.pt_los['footpaths'].apply(
            lambda l: sum([d[t] for t in l]))
        d = self.links['length'].to_dict()
        self.pt_los['in_vehicle_length'] = self.pt_los['link_path'].apply(
            lambda l: sum([d[t] for t in l]))
        self.pt_los['length'] = self.pt_los[
            ['access_length', 'footpath_length',  'in_vehicle_length']
        ].T.sum()

    def analysis_car_time(self):
        d = self.zone_to_road.set_index(['a', 'b'])['time'].to_dict()
        self.car_los['access_time'] = self.car_los['ntlegs'].apply(
            lambda l: sum([d[t] for t in l]))   
        d = self.road_links['time'].to_dict()
        self.car_los['in_vehicle_time'] = self.car_los['link_path'].apply(
            lambda l: sum([d[t] for t in l]))
        self.car_los['time'] = self.car_los[
            ['access_time', 'in_vehicle_time']
        ].T.sum()
    
    def analysis_car_length(self):
        d = self.zone_to_road.set_index(['a', 'b'])['distance'].to_dict()
        self.car_los['access_length'] = self.car_los['ntlegs'].apply(
            lambda l: sum([d[t] for t in l]))
        
        d = self.road_links['length'].to_dict()
        self.car_los['in_vehicle_length'] = self.car_los['link_path'].apply(
            lambda l: sum([d[t] for t in l]))

        self.car_los['length'] = self.car_los[
            ['access_length',  'in_vehicle_length']
        ].T.sum()

    @track_args
    def analysis_summary(self):
        """
        To perform on a model object once it is built and run,
        aggregate and analyses results.
            * requires: shared, zones, loaded_links, od_stack
            * builds: aggregated_shares, lines, economic_series
        """
        try: 
            self.aggregated_shares = engine.aggregate_shares(
                self.shared, self.zones)
        except AttributeError: 
            pass
        self.lines = analysis.tp_summary(self.loaded_links, self.od_stack)
        self.lines = analysis.analysis_tp_summary(self.lines)
        self.economic_series = analysis.economic_series(self.od_stack, self.lines)

    @track_args
    def analysis_desire(self, store_shp=False, **to_shp_kwarg):
        """
        Builds the desire matrix
            * requires: zones, shares
            * builds: neighborhood, macro_neighborhood
        """
        self.neighborhood = neighbors.Neighborhood(
            self.zones,
            self.volumes,
            volume_columns=['volume'],
            display_progress=False
        )
        zones = self.zones.copy()
        zones['geometry'] = zones['geometry'].apply(lambda g: g.buffer(1e-9))

        self.macro_neighborhood = neighbors.Neighborhood(
            zones,
            self.volumes,
            volume_columns=['volume'],
            display_progress=False,
            n_clusters=min(25, len(zones)),
            od_geometry=True)
        
        if store_shp:
            columns_to_keep = ['origin', 'destination', 'volume', 'volume_transit', 'geometry']
            self.desire_lines = self.neighborhood.volume[columns_to_keep].dropna(subset=['geometry'])

    @track_args
    def analysis_checkpoints(
        self,
        link_checkpoints=(),
        node_checkpoints=(),
        **loaded_links_and_nodes_kwargs
        ):

        """
        tree analysis (arborescences)
        :param link_checkpoints: mandatory transit links collection (set)
        :param nodes_checkpoints: mandatory transit nodes
        :param volume column: column of self.od_stack to assign
        :loaded_links_and_nodes_kwargs: ...

        example:
        ::
            sm.checkpoints(link_checkpoints = {}, node_checkpoints={41})
            export.assigned_links_nodes_to_shp(
                sm.checkpoint_links,
                sm.checkpoint_nodes,
                gis_path=gis_path,
                link_name='links_test.shp',
                node_name='nodes_test.shp'
        )
        """

        selected = engine.loaded_links_and_nodes(
            self.links,
            self.nodes,
            volumes=self.volumes,
            path_finder_stack=self.pt_los,
            link_checkpoints=set(link_checkpoints),
            node_checkpoints=set(node_checkpoints),
            **loaded_links_and_nodes_kwargs
        )

        self.checkpoint_links = selected[0]
        self.checkpoint_nodes = selected[1]

    def analysis_lines(self, line_columns='all', group_id='trip_id', *args, **kwargs):
        self.lines = export.build_lines(
            self.links, 
            line_columns=line_columns, 
            group_id=group_id,
            *args, **kwargs
        )

    def get_road_links(self, trip_id='trip_id'):
        l = self.links.copy()
        flat = []
        for key, links in l['road_link_list'].to_dict().items():
            flat += [(key, link) for link in links]

        core = pd.DataFrame(flat, columns=['transit', 'road'])

        merged = pd.merge(self.links, core, left_index=True, right_on='transit')
        merged = pd.merge(merged, self.road_links, left_on='road', right_index=True, suffixes=['_transit', ''])
        return merged[['a', 'b', 'transit', 'geometry', 'road', trip_id]]


    def get_lines_with_offset(self, width=1, trip_id='trip_id'):
        # get road_links
        l = self.get_road_links()
        l['ab'] = l.apply(lambda r: tuple(sorted([r['a'], r['b']])), axis=1)

        # line_tuples geometry
        line_tuples = l.groupby(['road'])[trip_id].agg(lambda s: tuple(sorted(tuple(s))))
        road_links = gpd.GeoDataFrame(self.road_links)
        road_links['line_tuple'] = line_tuples
        road_links = road_links.dropna(subset=['line_tuple']).copy()

        line_tuples = list(set(line_tuples))
        line_tuple_geometries = dict()
        for line_tuple in tqdm(line_tuples):
            
            # build sorted_edges
            edges = road_links.loc[road_links['line_tuple'] == line_tuple]
            sorted_road_links = []
            selected = self.links.loc[self.links['trip_id'] == line_tuple[0]]
            for road_link_list in selected.sort_values('link_sequence')['road_link_list']:
                for road_link in road_link_list:
                    sorted_road_links.append(road_link)
            sorted_edges = edges.loc[sorted_road_links].dropna(subset=['a', 'b'])
            
            line_tuple_geometries[line_tuple] = geometries.connected_geometries(sorted_edges)
        
        return geometries.geometries_with_side(line_tuple_geometries, width=width)

    def compute_arod_list(self):
        agency_dict = self.links['agency_id'].to_dict()
        route_dict = self.links['route_id'].to_dict()
        node_zone_dict = self.nodes['zone_id'].to_dict()
        df = self.pt_los[[ 'boardings', 'alightings', 'boarding_links', 'alighting_links']]
        leg_tuples = [tuple(zip(*r)) for r in df.values]
        
        values = []
        for leg in leg_tuples:
            agencies_od_lists = []

            for boarding_node, alighting_node,  boarding_link, alighting_link in leg:

                agency_id = agency_dict[boarding_link]
                route_id = route_dict[boarding_link]
                origin_id = node_zone_dict[boarding_node]
                destination_id = node_zone_dict[alighting_node]

                agencies_od_lists.append(
                    (agency_id, route_id, origin_id, destination_id)
                ) 
            values.append(agencies_od_lists)

        self.pt_los['arod_list'] = values    

    def compute_od_fares(self):
    
        # builds od fare graph to compute cheapest fare between o and d for a given agency
        fares = pd.merge(self.fare_rules, self.fare_attributes, on='fare_id')
        fare_graph_dict = {}
        for agency_id in self.fare_attributes['agency_id'].unique():
            df = fares.loc[fares['agency_id'] == agency_id].copy()
            dg = nx.DiGraph()
            dg.add_weighted_edges_from(df[['origin_id', 'destination_id', 'price']].values)
            all_pairs =  nx.all_pairs_dijkstra_path_length(dg)
            fare_graph_dict[agency_id] = dict(all_pairs)  
            
        def arod_list_to_aod_list(arod_list):
            if len(arod_list) == 0:
                return []

            aod = []
            agency, route, origin, destination = arod_list[0] 

            for a, r ,  o, d in arod_list[1:]:
                if a != agency:
                    aod.append((agency, origin, destination))
                    origin = o
                    agency = a
                destination = d

            aod.append((agency, origin, destination))
            return aod

        def od_price_breakdown(arod_list):
            aod_list = arod_list_to_aod_list(arod_list)

            breakdown = {}
            for agency, o, d in aod_list:
                try:
                    price = fare_graph_dict[agency][o][d]
                    try:
                        breakdown[agency] += price
                    except KeyError: # agency is seen for the first time
                        breakdown[agency] = price
                except KeyError: # their is no fare_graph for this a
                    price = np.nan
            return breakdown
        
        self.pt_los['od_fares'] = self.pt_los['arod_list'].apply(od_price_breakdown)

    def compute_route_fares(self):
        route_dict = self.links['route_id'].to_dict()

        # focus on fares rules that are given with unique within a route
        df = self.fare_rules.dropna(subset=['route_id'])
        df = df.loc[df['origin_id'].isnull() & df['destination_id'].isnull()]
        df = df.drop_duplicates(subset=['route_id'])
        route_fare_dict = {route_id: np.nan for route_id in route_dict.values()}
        route_update = df.set_index('route_id')['fare_id'].to_dict()
        route_fare_dict.update(route_update)
        
        # fare_attributes : speedups 
        transfers = self.fare_attributes.set_index('fare_id')['transfers'].to_dict()
        price = self.fare_attributes.set_index('fare_id')['price'].to_dict()

        def fare(count, allowed_transfers, price):
            return max(np.ceil(count / (allowed_transfers + 1))  , 1) * price

        def consecutive_counts(arod_list):
            # if their is no fare for a route, a nan is used
            # it is necessary in order to break the sequence in the loop
            fare_id_list = [route_fare_dict[route] for a, route, o, d in arod_list]
            if len(fare_id_list) == 0:
                return []
            current = fare_id_list[0]
            consecutive = []
            count = 1
            for fare_id in fare_id_list[1:] + [np.nan]:
                if fare_id != current:
                    consecutive.append((current, count))
                    count = 1
                else: 
                    count += 1
                current = fare_id
            return [(fare_id, count) for fare_id, count in consecutive if fare_id is not np.nan]

        def price_breakdown(consecutive_counts):
            breakdown = {}
            for fare_id, count in consecutive_counts:
                add = 0
                try: 
                    add = fare(count, transfers[fare_id], price[fare_id]) 
                    breakdown[fare_id] += add
                except KeyError: 
                    breakdown[fare_id] = add
            return breakdown

        def route_price_breakdown(arod_list):
            return price_breakdown(consecutive_counts(arod_list))
        
        self.pt_los['route_fares'] = self.pt_los['arod_list'].apply(route_price_breakdown)
    
    def analysis_pt_fare(self, keep_intermediate_results=True):
        self.compute_arod_list()
        self.compute_od_fares()
        self.compute_route_fares()
        
        values = self.pt_los[['route_fares', 'od_fares']].values
        self.pt_los['price'] = [
            sum(route_fares.values()) + sum(od_fares.values()) 
            for route_fares, od_fares in values
        ]

        if not keep_intermediate_results:
            del self.pt_los['arod_list']
            del self.pt_los['route_fares']
            del self.pt_los['od_fares']

    def generate_production_attraction_densities(self, volume_columns=None):
        if volume_columns is None:
            volume_columns = list(self.volumes.columns)
        prod = self.volumes[list(set(volume_columns + ['origin', 'destination']))].groupby(
            'origin', as_index=False
        ).sum()
        attr = self.volumes[list(set(volume_columns + ['origin', 'destination']))].groupby(
            'destination', as_index=False
        ).sum()
        # Add geometry
        prod = gpd.GeoDataFrame(
            prod.rename(columns={'origin': 'zone_id'}).merge(
                self.zones[['geometry']], left_on='zone_id', right_index=True
            )
        )
        attr = gpd.GeoDataFrame(
            attr.rename(columns={'destination': 'zone_id'}).merge(
                self.zones[['geometry']], left_on='zone_id', right_index=True
            )
        )
        # Compute densities
        for col in list(set(prod.columns).intersection(set(volume_columns))):
            prod[col + r'_d'] = prod[col] / prod.area * 10**6
            attr[col + r'_d'] = attr[col] / attr.area * 10**6
        
        self.production = prod
        self.attraction = attr


