
import pandas as pd
import numpy as np
from quetzal.engine import pathfinder
from quetzal.model import preparationmodel


class ParkRideModel(preparationmodel.PreparationModel):
    def node_transit_zone_edges(
        self,
        pr_nodes,
        reverse=False,
        boarding_time=0,
        alighting_time=0
    ):
        # link edges
        edges = pathfinder.link_edges(
            self.links,
            boarding_time=boarding_time,
            alighting_time=alighting_time
        )

        # connectors and footpaths
        zn = 'a' if reverse else 'b'
        ztr = self.zone_to_road.copy()
        ztr = ztr.loc[ztr[zn].isin(self.zones.index)]
        ztr = ztr[['a', 'b', 'time']].values.tolist()
        rtt = self.road_to_transit[['a', 'b', 'time']].values.tolist()
        footpaths = self.road_links[['a', 'b', 'walk_time']].values.tolist()

        return edges + ztr + footpaths + rtt

    def get_node_transit_zone(
        self, pr_nodes, reverse=False,
        boarding_time=0, alighting_time=0
    ):

        ntz_edges = self.node_transit_zone_edges(
            pr_nodes=pr_nodes, reverse=reverse,
            boarding_time=boarding_time, alighting_time=alighting_time
        )

        matrix, node_index = pathfinder.sparse_matrix(ntz_edges)

        zn = 'a' if reverse else 'b'

        zones = set(self.zones.index).intersection(self.zone_to_road[zn])
        sources, targets = (zones, pr_nodes) if reverse else (pr_nodes, zones)

        node_transit_zone = pathfinder.paths_from_graph(
            csgraph=matrix,
            node_index=node_index,
            sources=sources,
            targets=targets,
        )

        node_transit_zone['reverse'] = reverse
        return node_transit_zone

    def zone_road_node_edges(self, pr_nodes=None, reverse=False):

        # zn = 'a' keeps zone->road zn='b' keeps road->zone
        zn, pn = ('b', 'a') if reverse else ('a', 'b')
        ztr = self.zone_to_road.copy()
        ztr = ztr.loc[ztr[zn].isin(self.zones.index)][['a', 'b', 'time']]
        rtt = self.road_to_transit.copy()
        if pr_nodes is not None:
            rtt = rtt.loc[rtt[pn].isin(pr_nodes)]

        edges = ztr[['a', 'b', 'time']].values.tolist()
        edges += rtt[['a', 'b', 'time']].values.tolist()
        edges += self.road_links[['a', 'b', 'time']].values.tolist()
        return edges

    def get_zone_road_node(self, pr_nodes=None, reverse=False):
        zn = 'b' if reverse else 'a'
        zones = set(self.zones.index).intersection(self.zone_to_road[zn])

        zrt_edges = self.zone_road_node_edges(
            pr_nodes=pr_nodes, reverse=reverse)
        matrix, node_index = pathfinder.sparse_matrix(zrt_edges)

        sources, targets = (pr_nodes, zones) if reverse else (zones, pr_nodes)
        zone_road_node = pathfinder.paths_from_graph(
            csgraph=matrix,
            node_index=node_index,
            sources=sources,
            targets=targets
        )
        zone_road_node['reverse'] = reverse
        return zone_road_node

    def build_park_ride_shortcuts(
        self, pr_nodes,
        boarding_time=0, alighting_time=0
    ):
        # MORNING
        ntz = self.get_node_transit_zone(
            pr_nodes=pr_nodes, reverse=False,
            boarding_time=boarding_time, alighting_time=alighting_time
        )
        zrn = self.get_zone_road_node(pr_nodes=pr_nodes, reverse=False)

        # EVENING
        ntz_r = self.get_node_transit_zone(
            pr_nodes=pr_nodes, reverse=True,
            boarding_time=boarding_time, alighting_time=alighting_time
        )
        zrn_r = self.get_zone_road_node(pr_nodes=pr_nodes, reverse=True)

        self.node_transit_zone = pd.concat([ntz, ntz_r])
        self.zone_road_node = pd.concat([zrn, zrn_r])

    def combine_shortcuts(
        self,
        pr_nodes=None,
        ntlegs_penalty=1e9,
        cutoff=np.inf,
        od_set=None,
        parking_times=None,
        reverse=False,
    ):

        zrn, ntz = self.zone_road_node, self.node_transit_zone
        ntz.reset_index(drop=True, inplace=True)
        ntz.index = ['ntz_' + str(i) for i in ntz.index]
        zrn.reset_index(drop=True, inplace=True)
        zrn.index = ['zrn_' + str(i) for i in zrn.index]

        ntz = ntz.copy()
        ntz['length'] += ntlegs_penalty

        shortcuts = pd.concat([ntz, zrn])
        if reverse is not None:
            shortcuts = shortcuts.loc[shortcuts['reverse'] == reverse]

        # ADD PARKING TIME
        if parking_times is not None:
            right = pd.DataFrame(
                parking_times.items(),
                columns=['origin', 'parking_time']
            )
            shortcuts = pd.merge(shortcuts, right, on='origin', how='left')
            shortcuts['parking_time'].fillna(0, inplace=True)
            shortcuts['length'] += shortcuts['parking_time']

        if pr_nodes is not None:
            shortcuts.loc[
                shortcuts['origin'].isin(pr_nodes) |
                shortcuts['destination'].isin(pr_nodes)
            ]
        edges = shortcuts[['origin', 'destination', 'length']].values

        matrix, node_index = pathfinder.sparse_matrix(edges)

        s_path = shortcuts.set_index(
            ['origin', 'destination'])['path'].to_dict()

        def concatenate_path(path):
            try:
                return s_path[(path[0], path[1])] + s_path[(path[1], path[2])]
            except IndexError:
                return path

        zone_set = set(self.zones.index)
        paths = pathfinder.paths_from_graph(
            csgraph=matrix,
            node_index=node_index,
            sources=zone_set,
            targets=zone_set,
            cutoff=cutoff+ntlegs_penalty,
            od_set=od_set
        )
        paths['shortcut_path'] = paths['path']
        paths['path'] = [concatenate_path(p) for p in paths['shortcut_path']]
        paths = paths.loc[paths['origin'] != paths['destination']]
        paths['time'] = paths['length'] - ntlegs_penalty
        paths = paths.loc[paths['time'] <= cutoff]
        paths['parking_node'] = [path[1] for path in paths['shortcut_path']]
        self.pr_los = paths
