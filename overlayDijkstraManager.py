import dijkstraManager
import threading
from copy import deepcopy
import addressManagment

ospf_group_address = 'ff02::5'


class OverlayNode:
    def __init__(self, idx):
        self.idx = idx
        self.neighbors = []
        self.prefixes = {}  # {prefix:Prefix(),}
        self.isOverlay = True

    def add_neighbors(self, neighbors):
        for neighbor in neighbors:
            self.neighbors.append(neighbor)

    def remove_neighbors(self, neighbors):
        for neighbor in neighbors:
            if neighbor in self.neighbors:
                self.neighbors.remove(neighbor)

    def set_neighbors(self, neighbors):
        self.neighbors = deepcopy(neighbors)

    def add_prefixes(self, prefixes):  # [prefix objects]
        for prefix in prefixes:
            self.prefixes[prefix.address] = deepcopy(prefix)

    def remove_prefixes(self, prefixes):  # [prefix addresses]
        for prefix in prefixes:
            if prefix.address in self.prefixes:
                del self.prefixes[prefix.address]

    def update_prefix_cost(self, prefixes):  # [prefix objects]
        changes = False
        for prefix in prefixes:
            if prefix.metric != self.prefixes[prefix.address].metric:
                self.prefixes[prefix.address] = prefix
                changes = True
        return changes

    def set_prefixes(self, prefixes):
        for prefix in prefixes.values():
            self.prefixes[prefix.address] = prefix

    def add_connection(self):
        pass


class OverlayDijkstraManager:
    def __init__(self, selfNode, ospfDb):
        self.ospfDb = ospfDb
        self.nodes = {}  # entry = nodeID: node
        self.graph = dijkstraManager.Graph()
        self.changes = False
        self.work = True
        self.visited = {}
        self.path = {}
        self.graphPath = {}
        self.routeManager = OverlayRouteManager(ospfDb)

        self.init = self.add_node(selfNode, [])

        self.thread = threading.Thread(target=self.run, args=(self.nodes[selfNode],))
        self.thread.daemon = True
        self.thread.start()

    def add_node(self, id, neighbors):
        if id not in self.nodes:
            newNode = OverlayNode(id)
            newNode.set_neighbors(neighbors)
            self.graph.add_node(newNode)
            self.nodes[id] = newNode
            return newNode
        else:
            node = self.nodes[id]
            if len(node.neighbors) == 0:
                node.set_neighbors(neighbors)
            return node

    def del_node(self, id):
        node = self.nodes[id]
        self.graph.remove_node(node)
        del self.nodes[id]
        self.changes = True

    def update_node_add_neighbors(self, nodeId, neighbors):
        node = self.nodes[nodeId]
        node.add_neighbors(neighbors)
        self.changes = True

    def update_node_remove_neighbors(self, nodeId, neighbors):
        node = self.nodes[nodeId]
        node.remove_neighbors(neighbors)
        self.changes = True

    def update_node_add_prefixes(self, nodeId, prefixes):
        node = self.nodes[nodeId]
        node.add_prefixes(prefixes)
        self.changes = True

    def update_node_remove_prefixes(self, nodeId, prefixes):
        node = self.nodes[nodeId]
        node.remove_prefixes(prefixes)
        self.changes = True

    def update_node_prefix_cost(self, nodeId, prefixes):
        node = self.nodes[nodeId]
        if node.update_prefix_cost(prefixes):
            self.changes = True

    def add_edge(self, sNode, dNode, cost):
        node1 = self.nodes[sNode]
        try:
            node2 = self.nodes[dNode]
        except:
            node2 = self.add_node(dNode, [])
        self.graph.add_edge(node1, node2, cost)
        self.changes = True

    def remove_edge(self, sNode, dNode):
        node1 = self.nodes[sNode]
        node2 = self.nodes[dNode]
        self.graph.remove_edge(node1, node2)
        self.changes = True

    def update_edge_cost(self, sNode, dNode, newCost):
        node1 = self.nodes[sNode]
        node2 = self.nodes[dNode]
        if self.graph.change_cost(node1, node2, newCost):
            self.changes = True
            return
        else:
            return

    def next_hop(self, path, dest, init):
        nextHop = path[dest]
        previousHop = dest
        while nextHop != init:
            previousHop = nextHop
            nextHop = path[nextHop]
        return previousHop

    def print_edges(self):
        self.graph.print_edges()

    def clear_inter_area_lsas(self):
        for lsdb in self.ospfDb.lsdbs.values():
            lsdb.clear_inter_area_lsas()

    def run(self, initial):
        while self.work:
            if self.changes:
                self.visited, self.path = dijkstraManager.dijsktra(self.graph, initial, self.nodes)
                aux = {}
                for dest in self.path.keys():
                    aux[dest] = self.next_hop(self.path, dest, self.init)
                    self.graphPath = self.path
                self.path = aux
                self.routeManager.set_remote_destinations(self.visited)
                self.changes = False
                threading._sleep(5)
            else:
                threading._sleep(5)
        self.clear_inter_area_lsas()

    def __del__(self):
        self.work = False


class OverlayRouteManager:
    def __init__(self, ospfDb):
        self.ospfDb = ospfDb
        self.oldPrefixes = {}  # area: [prefix1, prefix2, ...]

    def calculate_routes(self, visited):
        out = {}  # {address: [intf, cost]}
        for dest in visited:
            for address in dest.prefixes:
                cost = int(visited[dest]) + int(dest.prefixes[address].metric)
                if address in out:
                    if cost >= out[address].metric:
                        pass
                    else:
                        prefix = deepcopy(dest.prefixes[address])
                        prefix.metric = cost
                        out[address] = prefix
                else:
                    prefix = deepcopy(dest.prefixes[address])
                    prefix.metric = cost
                    out[address] = prefix
        return out

    def create_inter_area_lsas(self, prefixes):
        if len(prefixes) > 0:
            for area in self.ospfDb.lsdbs.keys():
                out = {}
                for prefix in prefixes.values():
                    address = prefix.address
                    if address in self.ospfDb.localRoutingTable.keys():
                        if area == self.ospfDb.localRoutingTable[address].area:
                            addressManagment.print_service('\n prefix: ' + str(address))
                            addressManagment.print_service('Inter metric: ' + str(prefix.metric))
                            addressManagment.print_service('Intra metric: ' + str(self.ospfDb.localRoutingTable[address].cost))
                            addressManagment.print_service('\nRouter_linux: ')
                    if prefix.address not in out:
                        out[prefix.address] = prefix
                    else:
                        if prefix.metric < out[prefix.address]:
                            out[prefix.address] = prefix
                if len(out) > 0:
                    try:
                        oldPrefixes = self.oldPrefixes[area]
                    except:
                        oldPrefixes = {}
                    self.ospfDb.lsdbs[area].update_self_inter_area_prefix_lsas(out, oldPrefixes)
                    self.oldPrefixes[area] = out

    def set_remote_destinations(self, visited):
        prefixes = self.calculate_routes(visited)
        self.create_inter_area_lsas(prefixes)
