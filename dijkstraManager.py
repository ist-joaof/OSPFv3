from collections import defaultdict
from copy import deepcopy
import threading
import addressManagment
import linkStateDatabase
import datetime


class Node:
    def __init__(self, idx, neighbors, lsaIdx, isInterArea):
        self.type = None
        self.idx = idx  # RouterLSA --> RouterID; NetworkLSA --> DR RouterID.DR intfId
        self.isOverlay = False
        try:
            self.advRouter = lsaIdx.split('-')[1]
        except:
            self.advRouter = None
        self.isInterArea = isInterArea
        self.lsaIdx = lsaIdx
        self.address = {}  # {addr:Prefix(),}
        self.prefix = {}  # {prefix:Prefix(),}
        self.neighbors = neighbors  # [neighbornodeIdx,]
        self.interfaces = {}  # {intfId: cost}
        self.interAreaPrefixes = {} # {prefix: Prefix()}
        self.connection = 0

    def update_node(self, neighbors, lsaIdx):
        self.neighbors = neighbors
        self.lsaIdx = lsaIdx
        self.advRouter = lsaIdx.split('-')[1]

    def add_prefixes(self, prefix):
        pass

    def remove_prefixes(self, prefix):
        pass

    def add_inter_area_prefix(self, prefix):
        self.interAreaPrefixes[prefix.get_full_address()] = prefix

    def remove_inter_area_prefix(self, prefix):
        try:
            del self.interAreaPrefixes[prefix]
        except:
            addressManagment.print_service('no prefix ' + str(prefix) + ' on node ' + self.advRouter)

    def get_prefixes(self):
        pass

    def add_neighbor(self, neighbor):
        pass

    def remove_neighbor(self, neighbor):
        pass

    def add_interface(self, intfId, cost):
        if intfId not in self.interfaces.keys():
            self.interfaces[intfId] = cost
            return True
        else:
            return False

    def remove_interface(self, intfId):
        try:
            del self.interfaces[intfId]
            return True
        except:
            return False

    def update_interface_cost(self, intfId, cost):
        if intfId in self.interfaces.keys():
            if self.interfaces[intfId] != cost:
                self.interfaces[intfId] = cost
                return True
        return False

    def add_connection(self):
        self.connection += 1

    def remove_connection(self):
        self.connection -= 1

    def is_network(self):
        pass

    def print_node(self):
        print 'Node ' + str(self.idx) + ':'


class RouterNode(Node):
    def __init__(self, idx, neighbor, lsaIdx, isInterArea):
        Node.__init__(self, idx, neighbor, lsaIdx, isInterArea)
        self.type = linkStateDatabase.ROUTER_LSA
        self.prefix = None

    def add_prefixes(self, prefix):
        self.address[prefix.get_full_address()] = prefix

    def remove_prefixes(self, prefix):
        if type(prefix) is str:
            del self.address[prefix]
        else:
            del self.address[prefix.address]

    def add_neighbor(self, neighbor):
        if neighbor not in self.neighbors:
            self.neighbors = [neighbor]
            return True
        else:
            return False

    def remove_neighbor(self, neighbor):
        try:
            del self.neighbors[neighbor]
            return True
        except:
            return False

    def get_prefixes(self):
        return deepcopy(self.address)

    def is_network(self):
        return False

    def print_node(self):
        Node.print_node(self)
        for address in self.address.keys():
            print 'dest: ' + str(address) + '\tcost: ' + str(self.address[address].metric)
        print '\n'


class NetworkNode(Node):
    def __init__(self, idx, neighbor, lsaIdx, isInterArea):
        Node.__init__(self, idx, neighbor, lsaIdx, isInterArea)
        self.type = linkStateDatabase.NETWORK_LSA
        self.address = None

    def add_prefixes(self, prefix):
        self.prefix[prefix.address] = deepcopy(prefix)

    def remove_prefixes(self, prefix):
        if type(prefix) is str:
            del self.prefix[prefix]
        else:
            del self.prefix[prefix.address]

    def add_neighbor(self, neighbor):
        if neighbor not in self.neighbors:
            self.neighbors.append(neighbor)
            return True
        else:
            return False

    def remove_neighbor(self, neighbor):
        if neighbor in self.neighbors:
            self.neighbors.remove(neighbor)
            return True
        else:
            return False

    def get_prefixes(self):
        return deepcopy(self.prefix)

    def is_network(self):
        return True

    def print_node(self):
        Node.print_node(self)
        for prefix in self.prefix.keys():
            print 'dest: ' + str(prefix) + '\tcost: ' + str(self.prefix[prefix].metric)
        print '\n'


class Graph:
    def __init__(self):
        self.nodes = set()
        self.edges = defaultdict(list)  # {SNode: [DNode1, DNode2]}
        self.distances = {}

    def add_node(self, node):
        self.nodes.add(node)
        self.edges[node] = []
        return self

    def remove_node(self, node):
        if node in self.edges.keys():
            for dnode in self.edges[node]:
                self.remove_edge(node, dnode)
                addressManagment.print_service('removing edge from ' + str(node.idx) + ' to ' + str(dnode))
            del self.edges[node]
            self.nodes.remove(node)

    def add_edge(self, from_node, to_node, distance):
        if to_node not in self.edges[from_node]:
            self.edges[from_node].append(to_node)
        self.distances[(from_node, to_node)] = distance
        if not from_node.isOverlay:
            from_node.add_connection()
        return self

    def remove_edge(self, from_node, to_node):
        try:
            self.edges[from_node].remove(to_node)
            if not from_node.isOverlay:
                from_node.remove_connection()
            del self.distances[(from_node, to_node)]
        except:
            try:
                self.edges[from_node].remove(to_node)
                del self.distances[(from_node, to_node)]
            except:
                print 'failed to remove edge ' + from_node.idx + '->' + to_node + ' on overlay'
                pass

    def change_cost(self, from_node, to_node, newCost):
        try:
            self.distances[(from_node, to_node)]
        except:
            self.distances[(from_node, to_node)] = newCost
            return True
        if self.distances[(from_node, to_node)] != newCost:
            self.distances[(from_node, to_node)] = newCost
            return True
        else:
            return False

    def print_nodes(self):
        for node in self.nodes:
            node.print_node()
        print '\nRouter_linux: '

    def print_edges(self):
        for node in self.edges.keys():
            for dest in self.edges[node]:
                print str(node.idx) + ' --> ' + str(dest.idx) + ' | ' + str(self.distances[(node, dest)])


def dijsktra(graph, initial, nodeList):
    visited = {initial: 0}
    path = {}

    nodes = set(graph.nodes)

    while nodes:
        min_node = None
        for node in nodes:
            if node in visited:
                if min_node is None:
                    min_node = node
                elif visited[node] < visited[min_node]:
                    min_node = node
        if min_node is None:
            break
        nodes.remove(min_node)
        current_weight = visited[min_node]
        for edge in graph.edges[min_node]:
            try:
                weight = current_weight + graph.distances[(min_node, edge)]
            except:
                continue
            if type(edge) is str:
                try:
                    edge = nodeList[edge]
                except:
                    continue
            if edge not in visited or weight < visited[edge]:
                visited[edge] = weight
                path[edge] = min_node
    return visited, path


class RouteTable:
    def __init__(self, ospfdb, area):
        self.routes = {}  # {destPrefix:Route(),}
        self.prefixes = {}  # {destPrefix: Prefix()}
        self.ospfDB = ospfdb
        self.area = area

    def add_route(self, dest, intf, metric, install, isnexthop):
        if not install:
            return
        route = Route(intf, dest, metric, isnexthop)
        if dest.address not in self.prefixes.keys():
            self.prefixes[dest.address] = dest
            self.routes[dest.address] = route
        else:
            self.prefixes[dest.address] = dest
            self.routes[dest.address] = route

    def process_routes(self, oldTable, adjacencies):
        addressManagment.print_service('\nProcessing Routes...\n')
        newRoutes = set(self.routes.keys())
        addressManagment.print_service('New Routes: ' + str(newRoutes))
        if oldTable is None:
            for dest in self.routes.keys():
                try:
                    dest = self.prefixes[dest].dest
                except:
                    dest = self.prefixes[dest]
                route = self.routes[dest.address]
                if dest.address not in self.ospfDB.localRoutingTable.keys():
                    for neighbor in adjacencies.values():
                        if self.routes[dest].intf == neighbor.linkLocal:
                            if self.ospfDB.add_route(dest.address, route.metric, self.area):
                                addressManagment.add_route(dest.get_full_address(), neighbor, route.metric, False)
                            break
        else:
            oldRoutes = set(oldTable.routes.keys())
            routesToAdd = newRoutes - oldRoutes
            addressManagment.print_service('New Routes area ' + self.area + ': ' + str(routesToAdd))
            routesToUpdate = newRoutes.intersection(oldRoutes)
            addressManagment.print_service('Routes to update area ' + self.area + ': ' + str(routesToUpdate))
            routesToDelete = oldRoutes - newRoutes
            addressManagment.print_service('Routes to delete area ' + self.area + ': ' + str(routesToDelete))
            for dest in routesToAdd:    #routes to add
                try:
                    dest = self.prefixes[dest].dest
                except:
                    dest = self.prefixes[dest]
                route = self.routes[dest.address]
                if dest.address not in self.ospfDB.localRoutingTable.keys():
                    for neighbor in adjacencies.values():
                        if route.intf == neighbor.linkLocal:
                            if self.ospfDB.add_route(dest.address, route.metric, self.area):
                                addressManagment.add_route(dest.get_full_address(), neighbor, route.metric, route.isNextHop)
                            break
            for dest in routesToUpdate: # routes to update
                try:
                    dest = self.prefixes[dest].dest
                except:
                    dest = self.prefixes[dest]
                route = self.routes[dest.address]
                for neighbor in adjacencies.values():
                    if route.intf == neighbor.linkLocal:
                        if self.ospfDB.add_route(dest.address, route.metric, self.area):
                            addressManagment.update_cost(dest.get_full_address(), neighbor, route.metric, route.isNextHop)
                        break
            for dest in routesToDelete:
                if oldTable.routes[dest].intf[0] == 'f':
                    for neighbor in adjacencies.values():
                        if oldTable.routes[dest].intf == neighbor.linkLocal:
                            try:
                                route = oldTable.prefixes[dest].dest
                            except:
                                route = oldTable.prefixes[dest]
                            via = neighbor.linkLocal
                            dev = neighbor.intfId
                            if route.length == 128:
                                prefix = route.address
                            else:
                                prefix = route.address + '/' + str(route.length)
                            addressManagment.del_route_via(prefix, dev, via)
                            addressManagment.del_route(prefix, dev)
                            self.ospfDB.remove_route(route.address, self.area)
                            break
                else:
                    try:
                        route = oldTable.prefixes[dest].dest
                    except:
                        route = oldTable.prefixes[dest]
                    if route.length == 128:
                        prefix = route.address
                    else:
                        prefix = route.address + '/' + str(route.length)
                    addressManagment.del_route(prefix, oldTable.routes[dest].intf)
                    self.ospfDB.remove_route(route.address, self.area)
                addressManagment.print_service('Done\nRouter_Linux: ')


class Route:
    def __init__(self, intfId, dest, metric, isnexthop):
        self.intf = intfId  # Interface.intfId
        self.dest = dest
        self.metric = metric
        self.isNextHop = isnexthop


class RouteManager:
    def __init__(self, area):
        self.adjacencies = {}  # {neighborId: Adjacency(),}
        self.interfaces = {}  # {intfId: Adjacency}
        self.areaID = area

    def add_adjacency(self, neighbor, intf, linkLocal):
        if intf not in self.interfaces.keys():
            adj = Adjacency(intf, linkLocal)
        else:
            adj = self.interfaces[intf]
        adj.add_neighbor(neighbor)
        self.adjacencies[neighbor] = adj

    def remove_adjacency(self, neighbor, intf):
        if neighbor in self.adjacencies:
            del self.adjacencies[neighbor]
            self.interfaces[intf].remove_neighbor(neighbor)

    def check_route(self, dest, cost, ospfDB, newRouteTable, localAddr):
        prefix = dest.address
        try:
            if (newRouteTable.routes[prefix].metric == cost and not localAddr) or newRouteTable.routes[prefix].metric < cost:
                return False
        except:
            pass
        skip = False
        for addr in ospfDB.addressList:
            if prefix in addr:
                skip = True
                break
        if not skip:
            return True
        else:
            return False

    def install_routes(self, visited, path, initial, ospfDB, oldRouteTable):
        install = True
        newRouteTable = RouteTable(ospfDB, self.areaID)
        for dest in visited.keys():
            isNextHop = False
            if dest.idx != initial.idx:  # if not initial node
                if initial.idx in dest.neighbors:  # check if node is adjacent to initial node
                    isNextHop = True
                for prefix in dest.get_prefixes().values():
                    install = self.check_route(prefix, visited[dest], ospfDB, newRouteTable, True)
                    try:
                        intfId = self.adjacencies[path[dest].idx].linkLocal
                    except:
                        intfId = ospfDB.get_intf_address_for_neighbor(path[dest].idx, self.areaID)
                        addressManagment.print_service('got adjacement from intf')
                    if isNextHop:
                        newRouteTable.add_route(prefix, intfId, visited[dest], install, True)
                    else:
                        newRouteTable.add_route(prefix, intfId, visited[dest], install, False)
                for prefix in dest.interAreaPrefixes.values():
                    try:
                        intfId = self.adjacencies[path[dest].idx].linkLocal
                    except:
                        intfId = ospfDB.get_intf_address_for_neighbor(path[dest].idx, self.areaID)
                        addressManagment.print_service('got adjacement from intf')
                    install = self.check_route(prefix, visited[dest] + prefix.metric, ospfDB, newRouteTable, False)
                    if isNextHop:
                        newRouteTable.add_route(prefix, intfId, prefix.metric + visited[dest], install, True)
                    else:
                        newRouteTable.add_route(prefix, intfId, prefix.metric + visited[dest], install, False)
        if oldRouteTable is not None:
            newRouteTable.process_routes(oldRouteTable, self.adjacencies)
        else:
            newRouteTable.process_routes(None, self.adjacencies)
        return newRouteTable


class Adjacency:
    def __init__(self, intfId, linkLocal):
        self.intfId = intfId
        self.linkLocal = linkLocal
        self.neighbors = []

    def add_neighbor(self, neighbor):
        if neighbor not in self.neighbors:
            self.neighbors.append(neighbor)
            return True
        else:
            return False

    def remove_neighbor(self, neighbor):
        if neighbor in self.neighbors:
            self.neighbors.remove(neighbor)
            return True
        else:
            return False


class DijkstraManager:
    def __init__(self, selfNode, ospfDB, areaID):
        self.nodes = {}  # {nodeId: node}
        self.graph = Graph()
        self.routeManager = RouteManager(areaID)
        self.changes = False
        self.mainChange = False
        self.work = True
        self.visited = {}
        self.path = {}
        self.graphPath = {}
        self.areaID = areaID
        self.oldRouteTable = None

        ospfDB.get_lsdb(areaID).set_dijkstra_manager(self)

        self.init = self.create_self_router_node(ospfDB)

        self.thread = threading.Thread(target=self.run, args=(self.nodes[selfNode], ospfDB))
        self.thread.daemon = True
        self.thread.start()

    def __del__(self):
        self.work = False

    def get_route_manager(self):
        return self.routeManager

    def create_self_router_node(self, ospfDb):
        lsaIdx = linkStateDatabase.id(linkStateDatabase.ROUTER_LSA, ospfDb.routerId, '0.0.0.0')
        lsdb = ospfDb.get_lsdb(self.areaID)
        lsa = lsdb.routerLS[lsaIdx]
        neighbors = []
        node = self.add_node(linkStateDatabase.ROUTER_LSA, ospfDb.routerId, neighbors, lsaIdx, lsdb.isInterArea)
        self.nodes[node.idx] = node
        for intf in lsa.linkCount.values():
            if intf.neighborRouterId != '0.0.0.0':
                neighbor = intf.neighborRouterId + '.' + intf.neighborInterfaceId
                self.update_node_add_neighbor(node.idx, neighbor, intf.metric)
        return node

    def add_node(self, type, id, neighbors, lsaIdx, isInterArea):
        if id not in self.nodes:
            if type == linkStateDatabase.NETWORK_LSA:
                newNode = NetworkNode(id, neighbors, lsaIdx, isInterArea)
            else:
                newNode = RouterNode(id, neighbors, lsaIdx, isInterArea)
            self.graph.add_node(newNode)
            self.nodes[id] = newNode
            self.update_changes()
            return newNode
        else:
            node = self.nodes[id]
            if node.lsaIdx is None:
                node.update_node(neighbors, lsaIdx)
                self.graph.add_node(node)
                self.nodes[id] = node
                self.update_changes()
                node.isInterArea = isInterArea
                return node
            else:
                if node.isInterArea != isInterArea:
                    node.isInterArea = isInterArea
                    self.update_changes()
                    return node

    def remove_node(self, id):
        try:
            node = self.nodes[id]
        except:
            return
        self.graph.remove_node(node)
        del self.nodes[id]
        self.update_changes()

    def get_node(self, id):
        return self.nodes[id]

    def update_node_add_interface(self, nodeId, intfId, cost):
        node = self.nodes[nodeId]
        if node.add_interface(intfId, cost):
            self.update_changes()

    def update_node_remove_interface(self, nodeId, intfId):
        node = self.nodes[nodeId]
        if node.remove_interface(intfId):
            self.update_changes()

    def update_node_change_interface_cost(self, nodeId, intfId, newCost):
        node = self.nodes[nodeId]
        node.update_interface_cost(intfId, newCost)
        self.update_changes()

    def update_node_add_neighbor(self, nodeId, neighborId, cost):
        node = self.nodes[nodeId]
        node.add_neighbor(neighborId)
        self.add_edge(nodeId, neighborId, cost)
        self.update_changes()
        return True

    def update_node_is_inter_area(self, nodeId, newState):
        node = self.nodes[nodeId]
        node.isInterArea = newState
        self.update_changes()

    def update_node_remove_neighbor(self, nodeId, neighborId):
        node = self.nodes[nodeId]
        node.remove_neighbor(neighborId)
        self.remove_edge(nodeId, neighborId)
        addressManagment.print_service('Removing edge from ' + str(node.idx) + ' to ' + str(neighborId))
        self.update_changes()
        return True

    def add_edge(self, snode, dnode, cost):
        node1 = self.nodes[snode]
        node2 = dnode
        self.graph.add_edge(node1, node2, cost)
        self.update_changes()

    def remove_edge(self, snode, dnode):
        node1 = self.nodes[snode]
        node2 = dnode
        self.graph.remove_edge(node1, node2)
        self.update_changes()

    def edit_cost(self, snode, dnode, cost):
        node1 = self.nodes[snode]
        node2 = dnode
        self.graph.change_cost(node1, node2, cost)
        self.update_changes()

    def next_hop(self, path, dest, init):
        nextHop = path[dest]
        previousHop = dest
        routerNextHop = previousHop
        while nextHop != init:
            routerNextHop = previousHop
            previousHop = nextHop
            nextHop = path[nextHop]
        if previousHop.is_network():
            return routerNextHop
        else:
            return previousHop

    def add_prefix(self, nodeID, prefix):
        try:
            node = self.nodes[nodeID]
        except:
            node = None
        if node is None:
            if len(nodeID.split('.')) > 4:
                node = NetworkNode(nodeID, [], None, False)
            else:
                node = RouterNode(nodeID, [], None, False)
        self.nodes[node.idx] = node
        node.add_prefixes(prefix)
        self.update_changes()

    def remove_prefix(self, nodeID, prefix):
        node = self.nodes[nodeID]
        node.remove_prefixes(prefix)
        self.update_changes()

    def add_inter_area_prefix(self, nodeId, prefix):
        node = self.nodes[nodeId]
        node.add_inter_area_prefix(prefix)
        self.update_changes()

    def remove_inter_area_prefix(self, nodeId, prefix):
        node = self.nodes[nodeId]
        node.remove_inter_area_prefix(prefix)
        self.update_changes()

    def add_adjacency(self, neighbor, intfId, linkLocal):
        self.routeManager.add_adjacency(neighbor, intfId, linkLocal)

    def remove_adjacency(self, neighbor, intfId):
        self.routeManager.remove_adjacency(neighbor, intfId)

    def update_self_node_is_inter_area(self, nodeId, isInterArea):
        node = self.get_node(nodeId)
        node.isInterArea = isInterArea
        self.update_changes()

    def print_graph(self):
        out1 = {}
        out2 = {}
        for i in self.visited.keys():
            out1[i.idx] = self.visited[i]
        for i in self.path.keys():
            out2[i.idx] = self.path[i].idx
        print '\nArea ' + self.areaID
        print 'visited nodes:'
        print out1
        print 'next-hop:'
        print out2
        print '\nnodes:'
        for node in self.graph.nodes:
            print node.idx
        print '\nedges:'
        for node in self.graph.edges.keys():
            print '\t' + node.idx
            for edge in self.graph.edges[node]:
                print '\t\t' + edge

    def print_graph_nodes(self):
        self.graph.print_nodes()

    def print_nodes(self):
        for node in self.nodes.values():
            node.print_node()
        print '\nRouter_Linux: '

    def print_edges(self):
        self.graph.print_edges()

    def update_inter_area_nodes(self, global_db):
        overlaylsdb = global_db.overlayLsdb
        interAreas = {}
        prefixes = {}  # prefix:cost
        for node in self.visited.keys():
            if node.isInterArea and self.visited[node] != 0:
                interAreas[node.idx] = self.visited[node]
            aux = deepcopy(node.get_prefixes())
            for object in aux.values():
                prefix = object.get_full_address()
                if prefix not in prefixes.keys():
                    prefixes[prefix] = self.visited[node]
                else:
                    if prefixes[prefix] < self.visited[node]:
                        prefixes[prefix] = self.visited[node]
        overlaylsdb.update_neighbors(self.areaID, interAreas)
        overlaylsdb.update_prefixes(self.areaID, prefixes)

    def kill_route_manager(self, ospfDb):
        overlay = ospfDb.overlayLsdb
        self.work = False
        if ospfDb.isInterArea:
            selfnode = self.get_node(ospfDb.routerId)
            overlay.update_neighbors(self.areaID, {selfnode.idx: selfnode})
            overlay.update_prefixes(self.areaID, {})

    def update_changes(self):
        self.changes = True
        self.mainChange = True

    def refresh_routing(self):
        self.changes = True

    def run(self, initial, ospfDB):
        while self.work:
            if self.changes:
                self.visited, self.path = dijsktra(self.graph, initial, self.nodes)
                aux = {}
                for dest in self.path.keys():
                    aux[dest] = self.next_hop(self.path, dest, self.init)
                    self.graphPath = self.path
                self.path = aux
                self.oldRouteTable = self.routeManager.install_routes(self.visited, self.path, initial, ospfDB,
                                                                      self.oldRouteTable)
                if ospfDB.isInterArea:
                    self.update_inter_area_nodes(ospfDB)
                self.changes = False
                if self.mainChange:
                    for routeManager in ospfDB.routingGraph.values():
                        if routeManager.areaID != self.areaID:
                            routeManager.refresh_routing()
                    self.mainChange = False
                print datetime.datetime.now().strftime('%H:%M:%S.%f') + ' Area ' + str(
                    self.areaID) + ' tree and routes converged'
                threading._sleep(2)
            else:
                threading._sleep(2)
