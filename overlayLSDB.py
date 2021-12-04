import time
import threading
import packetManager
import overlayDijkstraManager
from copy import deepcopy
import linkStateDatabase

ospf_group_address = 'ff02::5'
SEQUENCE_NUMBER = 2147483649
ABR_LSA = int(0x400a)
PREFIX_LSA = int(0x400b)
ASBR_LSA = int(0x400c)


class OverlayLSDB:
    def __init__(self, ospfdb):
        self.ospfDB = ospfdb
        self.overlayNeighbors = []
        self.abrLSID = 0
        self.asbrLSID = 0
        self.overlayRouteManager = None

        self.abrLS = {}
        self.prefixLS = {}
        self.asbrLS = {}

        self.lsRequests = {}  # routerID:[]

        self.neighors = {}  # neighborId:neighbor
        self.oldNeighbors = {}  # {areaId:{routerId:cost}}
        self.oldPrefixes = {}  # {address/range: cost}

        self.dbs = {ABR_LSA: self.abrLS, PREFIX_LSA: self.prefixLS, ASBR_LSA: self.asbrLS}

        self.lsdb_lock = threading.Lock()
        self.abrls_lock = threading.Lock()
        self.prefixls_lock = threading.Lock()
        self.asbrls_lock = threading.Lock()

        self.locks = {ABR_LSA: self.abrls_lock, PREFIX_LSA: self.prefixls_lock, ASBR_LSA: self.asbrls_lock}

        self.deadLSAs = {}
        self.distanceTable = {}  # routerId: dist

        self.timer_thread = threading.Thread(target=self.run, args=())
        self.timer_thread.daemon = True
        self.timer_thread.start()

    def run(self):
        dbs = self.dbs.values()
        while True:
            for db in dbs:
                if len(db) > 0:
                    for lsa in db.values():
                        lsa.age += 1
                        if lsa.age >= 3600:
                            self.delete_lsa(db, lsa.idx)
                        elif lsa.advRouter == self.ospfDB.routerId and lsa.age == 1800:
                            self.renew_lsa(lsa)
            self.check_for_abr_updates()
            time.sleep(1)

    def get_dbs(self):
        return self.dbs

    def create_route_manager(self):
        self.overlayRouteManager = overlayDijkstraManager.OverlayDijkstraManager(self.ospfDB.routerId, self.ospfDB)
        self.ospfDB.overlayGraph = self.overlayRouteManager

    def set_dijkstra_manager(self, dijkstra):
        self.overlayRouteManager = dijkstra

    def check_for_abr_updates(self):
        idx = None
        neighbors_to_remove = []
        for neighbor in self.neighors.values():
            if neighbor.change:
                if neighbor.remove:
                    idx = self.update_self_abrls_remove_neighbor(neighbor.nodeId)
                    neighbors_to_remove.append(neighbor.nodeId)
                else:
                    idx = self.update_self_abrls_add_neighbor(neighbor.nodeId, neighbor.bestCost)
                    neighbor.change = False
        if idx is not None:
            for node in neighbors_to_remove:
                self.neighors.pop(node)
            self.send_update(idx)

    def update_neighbors(self, area, update):
        try:
            oldSet = set(self.oldNeighbors[area].keys())
        except:
            oldSet = set()
        newSet = set(update.keys())
        for neighbor in newSet - oldSet:  # nodes to add
            if neighbor not in self.neighors.keys():
                neighborObject = Neighbor(neighbor, area, update[neighbor])
                self.neighors[neighbor] = neighborObject
            else:
                neighborObject = self.neighors[neighbor]
                neighborObject.add_area(area, update[neighbor])
        for neighbor in newSet.intersection(oldSet):  # nodes to update
            if update[neighbor] != self.oldNeighbors[area][neighbor]:
                neighborObject = self.neighors[neighbor]
                neighborObject.add_area(area, update[neighbor])
        for neighbor in oldSet - newSet:  # nodes to delete
            neighborObject = self.neighors[neighbor]
            neighborObject.remove_area(area)
        self.oldNeighbors[area] = deepcopy(update)

    def update_prefixes(self, area, update):
        idx = None
        try:
            oldSet = set(self.oldPrefixes[area].keys())
        except:
            oldSet = set()
        newSet = set(update.keys())
        for prefix in newSet - oldSet:  # prefixes to add
            address, length = prefix.split('/')
            idx = self.update_self_prefixls_add_prefix(update[prefix], length, 0, address)
        for prefix in oldSet - newSet:  # prefixes to delete
            idx = self.update_self_prefixls_remove_prefix(prefix.split('/')[0])
        for prefix in newSet.intersection(oldSet):  # prefixs to update
            if update[prefix] != self.oldPrefixes[area][prefix]:
                idx = self.update_self_prefixls_update_prefix_cost(prefix.split('/')[0], update[prefix])
        if idx is not None:
            lsa = self.get_lsa(idx)
            lsa.update()
            self.send_update(idx)
        self.oldPrefixes[area] = deepcopy(update)

    def delete_lsa(self, db, key):
        lsa = db[key]
        self.deadLSAs[key] = lsa
        del db[key]

    def ls_request(self, rid, lsType, advRouter, lsID, seq):
        idx = linkStateDatabase.id(lsType, advRouter, lsID)
        if rid not in self.lsRequests.keys():
            self.lsRequests[rid] = []
        db = self.dbs[lsType]
        if idx not in db.keys():
            self.lsRequests[rid].append(idx)
        else:
            ls = db[idx]
            if ls.sequenceNumber <= seq:
                self.lsRequests[rid].append(idx)

    def get_ls_requests(self, rid):
        out = self.lsRequests[rid]
        self.lsRequests[rid] = []
        return out

    def get_lsa(self, idx):
        table = int(idx.split('-')[0])
        db = self.dbs[table]
        lock = self.locks[table]
        lock.acquire()
        try:
            lsa = db[idx]
        except:
            lsa = None
        lock.release()
        return lsa

    def set_lsa(self, idx, lsa):
        table = int(idx.split('-')[0])
        db = self.dbs[table]
        lock = self.locks[table]
        lock.acquire()
        db[idx] = lsa
        lock.release()

    def renew_lsa(self, lsa):
        lsa.update()
        self.send_update(lsa)

    def kill_lsa(self, idx):
        lsType = int(idx.split('-')[0])
        table = self.dbs[lsType]
        lsa = table[idx]
        lsa.kill_lsa()

    def kill_self_overlay_lsas(self):
        idxs = []
        idx = self.kill_self_abrlsa()
        if idx is not None:
            idxs.append(idx)
        idx = self.kill_self_prefixlsa()
        if idx is not None:
            idxs.append(idx)
        idx = self.kill_self_asbrlsa()
        if idx is not None:
            idxs.append(idx)
        return idxs

    def send_overlay_dead_lsas(self, lsas):
        for intf in self.ospfDB.interfaceList.values():
            intf.updateManager.send_multicast_kill_lsa(intf, lsas)

    def process_intra_area_dijkstra(self, visited):
        for node in visited.keys():
            if not node.is_network():
                self.distanceTable[node.idx] = visited[node]
        pass

    def kill_overlay_routemanager(self):
        self.overlayRouteManager.work = False

    def create_abrls(self):
        lsid = '0.0.0.0'
        idx = linkStateDatabase.id(ABR_LSA, self.ospfDB.routerId, lsid)
        if idx not in self.abrLS.keys():
            lsa = ABRLSA(self.ospfDB.routerId, 0, SEQUENCE_NUMBER, lsid, {})
            self.set_lsa(idx, lsa)
        self.send_update(idx)

    def update_self_abrls_add_neighbor(self, neighbor, cost):
        routerId = self.ospfDB.routerId
        idx = linkStateDatabase.id(ABR_LSA, routerId, '0.0.0.0')
        lsa = self.get_lsa(idx)
        lsa.add_neighbor(neighbor, cost)
        lsa.update()
        self.overlayRouteManager.update_node_add_neighbors(routerId, [neighbor])
        self.overlayRouteManager.add_edge(routerId, neighbor, cost)
        return idx

    def update_self_abrls_remove_neighbor(self, neighbor):
        routerId = self.ospfDB.routerId
        idx = linkStateDatabase.id(ABR_LSA, routerId, '0.0.0.0')
        lsa = self.get_lsa(idx)
        lsa.remove_neighbor(neighbor)
        lsa.update()
        self.overlayRouteManager.update_node_remove_neighbors(routerId, [neighbor])
        self.overlayRouteManager.remove_edge(routerId, neighbor)
        return idx

    def update_self_abrls_update_neighbor_cost(self, neighbor, newCost):
        routerId = self.ospfDB.routerId
        idx = linkStateDatabase.id(ABR_LSA, routerId, '0.0.0.0')
        lsa = self.get_lsa(idx)
        lsa.update_neighbor_cost(routerId, neighbor, newCost)
        lsa.update()
        self.overlayRouteManager.update_edge_cost()
        return idx

    def update_abrls(self, advRouter, age, seq, lsid, neighborList, dead):
        idx = linkStateDatabase.id(ABR_LSA, advRouter, lsid)
        oldLSA = self.get_lsa(idx)
        if oldLSA is not None and oldLSA.sequenceNumber >= seq:
            return
        lsa = ABRLSA(advRouter, age, seq, lsid, neighborList)
        if not dead:
            self.set_lsa(idx, lsa)
        else:
            self.set_lsa(idx, lsa)
            return
        if self.ospfDB.isInterArea:
            if oldLSA is None:
                self.overlayRouteManager.add_node(advRouter, neighborList.keys())
                for neighbor in set(neighborList.keys()):  # neighbors to add
                    self.overlayRouteManager.update_node_add_neighbors(advRouter, [neighbor])
                    self.overlayRouteManager.add_edge(advRouter, neighbor, neighborList[neighbor])
            else:
                newNeighbors = set(neighborList.keys())
                oldNeighbors = set(oldLSA.neighborList.keys())
                for neighbor in (newNeighbors - oldNeighbors):  # neighbors to add
                    self.overlayRouteManager.update_node_add_neighbors(advRouter, [neighbor])
                    self.overlayRouteManager.add_edge(advRouter, neighbor, neighborList[neighbor])
                for neighbor in newNeighbors.intersection(oldNeighbors):    # neighbors to update
                    self.overlayRouteManager.update_edge_cost(advRouter, neighbor, neighborList[neighbor])
                for neighbor in (oldNeighbors- newNeighbors):   # neighbors to remove
                    self.overlayRouteManager.update_node_remove_neighbors(advRouter, neighbor)
                    self.overlayRouteManager.remove_edge(advRouter, neighbor)

    def kill_self_abrlsa(self):
        idx = linkStateDatabase.id(ABR_LSA, self.ospfDB.routerId, '0.0.0.0')
        if idx in self.abrLS.keys():
            self.kill_lsa(idx)
            return idx
        else:
            return None

    def create_prefixls(self):
        lsid = '0.0.0.0'
        idx = linkStateDatabase.id(PREFIX_LSA, self.ospfDB.routerId, lsid)
        if idx not in self.prefixLS.keys():
            lsa = PrefixLSA(self.ospfDB.routerId, 0, SEQUENCE_NUMBER, lsid, {})
            self.set_lsa(idx, lsa)
        else:
            lsa = self.get_lsa(idx)
        return lsa

    def update_self_prefixls_add_prefix(self, metric, length, options, address):
        routerId = self.ospfDB.routerId
        idx = linkStateDatabase.id(PREFIX_LSA, routerId, '0.0.0.0')
        lsa = self.get_lsa(idx)
        if lsa is None:
            lsa = self.create_prefixls()
        lsa.add_prefix(metric, length, options, address)
        self.overlayRouteManager.update_node_add_prefixes(routerId, [lsa.prefixes[address]])
        return idx

    def update_self_prefixls_remove_prefix(self, address):
        routerId = self.ospfDB.routerId
        idx = linkStateDatabase.id(PREFIX_LSA, routerId, '0.0.0.0')
        lsa = self.get_lsa(idx)
        if lsa is None:
            lsa = self.create_prefixls()
            return
        self.overlayRouteManager.update_node_remove_prefixes(routerId, [lsa.prefixes[address]])
        lsa.remove_prefix(address)
        return idx

    def update_self_prefixls_update_prefix_cost(self, address, newCost):
        routerId = self.ospfDB.routerId
        idx = linkStateDatabase.id(PREFIX_LSA, routerId, '0.0.0.0')
        lsa = self.get_lsa(idx)
        lsa.update_prefix_cost(address, newCost)
        self.overlayRouteManager.update_node_prefix_cost(routerId, [lsa.prefixes[address]])
        return idx

    def update_prefixls(self, advRouter, age, seq, lsid, prefixes, dead):
        idx = linkStateDatabase.id(PREFIX_LSA, advRouter, lsid)
        oldLSA = self.get_lsa(idx)
        if oldLSA is not None and oldLSA.sequenceNumber >= seq:
            return
        lsa = PrefixLSA(advRouter, age, seq, lsid, prefixes)
        if not dead:
            self.set_lsa(idx, lsa)
        else:
            self.set_lsa(idx, lsa)
            return
        if self.ospfDB.isInterArea:
            if oldLSA is None:
                self.overlayRouteManager.update_node_add_prefixes(advRouter, prefixes.values())
            else:
                newPrefixes = set(prefixes.keys())
                oldPrefixes = set(oldLSA.prefixes.keys())
                prefixesToAdd = newPrefixes - oldPrefixes
                if len(prefixesToAdd) > 0:
                    out = []
                    for prefix in prefixesToAdd:
                        out.append(prefixes[prefix])
                    self.overlayRouteManager.update_node_add_prefixes(advRouter, out)
                prefixesToUpdate = newPrefixes.intersection(oldPrefixes)
                if len(prefixesToUpdate) > 0:
                    out = []
                    for prefix in prefixesToUpdate:
                        out.append(prefixes[prefix])
                    self.overlayRouteManager.update_node_prefix_cost(advRouter, out)
                prefixesToRemove = oldPrefixes - newPrefixes
                if len(prefixesToRemove) > 0:
                    out = []
                    for prefix in prefixesToRemove:
                        out.append(oldLSA.prefixes[prefix])
                    self.overlayRouteManager.update_node_remove_prefixes(advRouter, out)

    def kill_self_prefixlsa(self):
        idx = linkStateDatabase.id(PREFIX_LSA, self.ospfDB.routerId, '0.0.0.0')
        if idx in self.prefixLS.keys():
            self.kill_lsa(idx)
            return idx
        else:
            return None

    def create_asbrls(self):
        lsid = '0.0.0.0'
        idx = linkStateDatabase.id(ASBR_LSA, self.ospfDB.routerId, lsid)
        if idx not in self.asbrLS.keys():
            lsa = ASBRLSA(self.ospfDB.routerId, 0, SEQUENCE_NUMBER, lsid, {})
            self.set_lsa(idx, lsa)
        return

    def update_self_asbrls_add_neighbor(self, neighbor, cost):
        idx = linkStateDatabase.id(ASBR_LSA, self.ospfDB.routerId, '0.0.0.0')
        lsa = self.get_lsa(idx)
        lsa.add_neighbor(neighbor, cost)
        lsa.update()

    def update_self_asbrls_remove_neighbor(self, neighbor):
        idx = linkStateDatabase.id(ABR_LSA, self.ospfDB.routerId, '0.0.0.0')
        lsa = self.get_lsa(idx)
        lsa.remove_neighbor(neighbor)

    def update_self_asbrls_update_neighbor_cost(self, neighbor, newCost):
        idx = linkStateDatabase.id(ASBR_LSA, self.ospfDB.routerId, '0.0.0.0')
        lsa = self.get_lsa(idx)
        lsa.update_neighbor_cost(neighbor, newCost)
        lsa.update()

    def update_asbrls(self, advRouter, age, seq, lsid, neighbors, dead):
        idx = linkStateDatabase.id(ASBR_LSA, advRouter, lsid)
        oldLSA = self.get_lsa(idx)
        if oldLSA is not None and oldLSA.sequenceNumber >= seq:
            return
        lsa = ASBRLSA(advRouter, age, seq, lsid, neighbors)
        if not dead:
            self.set_lsa(idx, lsa)
        else:
            self.set_lsa(idx, lsa)
            return

    def kill_self_asbrlsa(self):
        idx = linkStateDatabase.id(ASBR_LSA, self.ospfDB.routerId, '0.0.0.0')
        if idx in self.asbrLS.keys():
            self.kill_lsa(idx)
            return idx
        else:
            return None

    def send_update(self, idx):
        if type(idx) is not list:
            idx = [idx]
        for intf in self.ospfDB.interfaceList.values():
            intf.updateManager.send_multicast_update(intf, idx)

    def print_overlay_lsdb(self):
        print '\t\t\t\t\tOverlay LSDB'
        if len(self.abrLS) > 0:
            print '\n\t\t\t\tArea Border Router Link States (Overlay)'
            print 'ADV Router\t\tAge\t\tSeq#\t\tLink ID\t\t#Neighbors'
            for idx in sorted(self.abrLS.keys()):
                self.abrLS[idx].print_lsa()
        if len(self.prefixLS) > 0:
            print '\n\t\t\t\tPrefix Link States (Overlay)'
            print 'ADV Router\t\tAge\t\tSeq#\t\tLink ID\t\t#Prefixes'
            for idx in sorted(self.prefixLS.keys()):
                self.prefixLS[idx].print_lsa()
        if len(self.asbrLS) > 0:
            print '\n\t\t\t\tAutonumous System Border Router Link States (Overlay)'
            print 'ADV Router\t\tAge\t\tSeq#\t\tLink ID\t\t#ABRSs'
            for idx in sorted(self.asbrLS.keys()):
                self.asbrLS[idx].print_lsa()

    def print_lsas(self, type):
        table = self.dbs[type]
        if table:
            for lsa in table.values():
                lsa.print_lsa_detailed()


class Neighbor:
    def __init__(self, nodeId, area, cost):
        self.nodeId = nodeId
        self.areas = {area: int(cost)}
        self.bestCost = cost
        self.change = True
        self.remove = False

    def add_area(self, area, cost):
        if area not in self.areas.keys():
            self.areas[area] = cost
            self.remove = False
            if cost < self.bestCost:
                self.bestCost = cost
                self.change = True
        else:
            initialCost = self.bestCost
            self.areas[area] = cost
            bestCost = 1000
            for areaCost in self.areas.values():
                if areaCost < bestCost:
                    bestCost = areaCost
            if bestCost != initialCost:
                self.bestCost = bestCost
                self.change = True

    def remove_area(self, area):
        areaCost = self.areas[area]
        del self.areas[area]
        if len(self.areas) == 0:
            self.remove = True
            self.change = True
            self.bestCost = 1000
            return
        if self.bestCost == areaCost:
            best = 1000
            for cost in self.areas.values():
                if cost < best:
                    best = cost
            self.change = True


class ABRLSA(linkStateDatabase.LSA):
    def __init__(self, advRouter, age, sequenceNumber, lsid, neighborList):
        linkStateDatabase.LSA.__init__(self, ABR_LSA, advRouter, age, sequenceNumber)
        self.neighborList = neighborList  # {neighbor: cost}
        self.lsid = lsid

        self.idx = linkStateDatabase.id(self.lsType, advRouter, lsid)

    def add_neighbor(self, neighbor, cost):
        self.lock_lsa()
        self.neighborList[neighbor] = cost
        self.unlock_lsa()

    def remove_neighbor(self, neighbor):
        self.lock_lsa()
        try:
            del self.neighborList[neighbor]
            self.unlock_lsa()
        except:
            self.unlock_lsa()

    def get_neighbor_list(self):
        out = None
        self.lock_lsa()
        out = self.neighborList
        self.unlock_lsa()
        return out

    def update_neighbor_cost(self, neighbor, newCost):
        self.lock_lsa()
        self.neighborList[neighbor] = int(newCost)
        self.unlock_lsa()

    def get_neighbor_cost(self, neighbor):
        self.lock_lsa()
        out = self.neighborList[neighbor]
        self.unlock_lsa()
        return out

    def process_delete(self, db):
        nodeId = self.advRouter
        db.routeManager.remove_node(nodeId)

    def package_lsa(self, isfull):
        linkStateDatabase.LSA.package_lsa(self, isfull)
        lsa = packetManager.LSAType10(self.lsaHeader)
        return lsa.build(self, isfull)

    def print_lsa(self):
        self.lock_lsa()
        linkStateDatabase.LSA.print_lsa(self)
        print(self.advRouter + '\t\t\t' + str(self.age) + '\t\t' + hex(self.sequenceNumber) + '\t' + str(
            self.lsid) + '\t\t' + str(len(self.neighborList.keys())))
        self.unlock_lsa()

    def print_lsa_detailed(self):
        self.lock_lsa()
        print('\nABR LSA ' + self.advRouter + '\n')
        linkStateDatabase.LSA.print_lsa_detailed(self)
        for neighbor in sorted(self.neighborList.keys()):
            print('\tNeighbor: ' + neighbor + '\tCost: ' + str(self.neighborList[neighbor]))
        self.unlock_lsa()


class PrefixLSA(linkStateDatabase.LSA):
    def __init__(self, advRouter, age, sequeceNumber, lsid, prefixes):
        linkStateDatabase.LSA.__init__(self, PREFIX_LSA, advRouter, age, sequeceNumber)
        self.prefixes = prefixes  # {prefixAddr: Prefix}
        self.lsid = lsid

        self.idx = linkStateDatabase.id(self.lsType, advRouter, lsid)

    def add_prefix(self, metric, length, options, address):
        prefix = linkStateDatabase.Prefix(address, length, metric, options)
        self.lock_lsa()
        self.prefixes[address] = prefix
        self.unlock_lsa()

    def remove_prefix(self, address):
        self.lock_lsa()
        del self.prefixes[address]
        self.unlock_lsa()

    def update_prefix_cost(self, address, newCost):
        self.lock_lsa()
        prefix = self.prefixes[address]
        prefix.update_metric(newCost)
        self.unlock_lsa()

    def process_delete(self, db):
        node = db.routeManager.get_node(self.advRouter)
        for prefix in node.get_prefixes().values():
            db.routeManager.remove_prefix(node, prefix)

    def package_lsa(self, isfull):
        linkStateDatabase.LSA.package_lsa(self, isfull)
        lsa = packetManager.LSAType11(self.lsaHeader)
        return lsa.build(self, isfull)

    def print_lsa(self):
        self.lock_lsa()
        linkStateDatabase.LSA.print_lsa(self)
        print(self.advRouter + '\t\t\t' + str(self.age) + '\t\t' + hex(self.sequenceNumber) + '\t' + str(
            self.lsid) + '\t\t' + str(len(self.prefixes.keys())))
        self.unlock_lsa()

    def print_lsa_detailed(self):
        self.lock_lsa()
        print('\nPrefix LSA ' + self.advRouter + '\n')
        linkStateDatabase.LSA.print_lsa_detailed(self)
        for prefix in self.prefixes.values():
            prefix.print_prefix()
        self.unlock_lsa()


class ASBRLSA(linkStateDatabase.LSA):
    def __init__(self, advRouter, age, sequenceNumber, lsid, neighbors):
        linkStateDatabase.LSA.__init__(self, ASBR_LSA, advRouter, age, sequenceNumber)
        self.neighborList = neighbors  # {neighborId: cost}
        self.lsid = lsid

        self.idx = linkStateDatabase.id(self.lsType, advRouter, lsid)

    def add_neighbor(self, neighbor, cost):
        self.lock_lsa()
        self.neighborList[neighbor] = cost
        self.unlock_lsa()

    def remove_neighbor(self, neighbor):
        self.lock_lsa()
        del self.neighborList[neighbor]
        self.unlock_lsa()

    def get_neighbor_list(self):
        out = None
        self.lock_lsa()
        out = self.neighborList
        self.unlock_lsa()
        return out

    def update_neighbor_cost(self, neighbor, newCost):
        self.lock_lsa()
        self.neighborList[neighbor] = int(newCost)
        self.unlock_lsa()

    def get_neighbor_cost(self, neighbor):
        self.lock_lsa()
        out = self.neighborList[neighbor]
        self.unlock_lsa()
        return out

    def process_delete(self, db):
        nodeId = self.advRouter
        db.routeManager.remove_node(nodeId)

    def package_lsa(self, isfull):
        linkStateDatabase.LSA.package_lsa(self, isfull)
        lsa = packetManager.LSAType10(self.lsaHeader)
        return lsa.build(self, isfull)

    def print_lsa(self):
        self.lock_lsa()
        linkStateDatabase.LSA.print_lsa(self)
        print(self.advRouter + '\t\t\t' + str(self.age) + '\t\t\t' + hex(self.sequenceNumber) + '\t' + str(
            len(self.neighborList)))
        self.unlock_lsa()

    def print_lsa_detailed(self):
        self.lock_lsa()
        print('\nASBR LSA ' + self.advRouter + '\n')
        linkStateDatabase.LSA.print_lsa_detailed(self)
        for neighbor in sorted(self.neighborList.keys()):
            print('\tNeighbor: ' + neighbor + '\tCost: ' + str(self.neighborList[neighbor]))
        self.unlock_lsa()
