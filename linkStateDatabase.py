import threading
import interface
from copy import deepcopy
import time
import packetManager
import addressManagment

SEQUENCE_NUMBER = 2147483649
ROUTER_LSA = int(0x2001)
NETWORK_LSA = int(0x2002)
INTER_AREA_PREFIX_LSA = int(0x2003)
LINK_LSA = int(0x0008)
INTRA_AREA_PREFIX_LSA = int(0x2009)


class LSDB:
    def __init__(self, area, ospfdb):
        self.ospfDb = ospfdb
        self.area = area
        self.routerLSid = 0
        self.interAreaLSid = 0
        self.intraAreaLSid = 0
        self.interfaceList = []
        self.routeManager = None
        self.isInterArea = ospfdb.isInterArea
        self.overlayLsdb = ospfdb.overlayLsdb
        self.deadLSAs = {}  # {ReqId:lsa}

        self.routerLS = {}  # {idx: routerLSA}
        self.networkLS = {}  # {idx: networkLSA}
        self.linkLS = {}  # {idx: linkLSA}
        self.intraAreaLS = {}  # {idx: intraAreaLSA}
        self.interAreaPrefixLS = {}  # {idx: interAreaPrefixLSA}
        self.interAreaRouterLS = {}  # {idx: interAreaRouterLSA}

        self.lsRequests = {}  # routerID:[]
        self.interAreaPrefixLsid = {}   # prefix: lsid

        self.dbs = {ROUTER_LSA: self.routerLS, NETWORK_LSA: self.networkLS,
                    INTER_AREA_PREFIX_LSA: self.interAreaPrefixLS,
                    LINK_LSA: self.linkLS, INTRA_AREA_PREFIX_LSA: self.intraAreaLS}

        self.lsdb_lock = threading.Lock()
        self.router_lsa_lock = threading.Lock()
        self.network_lsa_lock = threading.Lock()
        self.link_lsa_lock = threading.Lock()
        self.interarea_lsa_lock = threading.Lock()
        self.intraarea_lsa_lock = threading.Lock()

        self.locks = {ROUTER_LSA: self.router_lsa_lock, NETWORK_LSA: self.network_lsa_lock,
                      INTER_AREA_PREFIX_LSA: self.interarea_lsa_lock, LINK_LSA: self.link_lsa_lock,
                      INTRA_AREA_PREFIX_LSA: self.intraarea_lsa_lock}

        self.timer_thread = threading.Thread(target=self.run, args=())
        self.timer_thread.daemon = True
        self.timer_thread.start()

    def run(self):
        lsdb = self.dbs.values()
        while True:
            for db in lsdb:
                if len(db) > 0:
                    for lsa in db.values():
                        lsa.age += 1
                        if lsa.age >= 3600:
                            self.delete_lsa(db, lsa.idx)
                        elif lsa.advRouter == self.ospfDb.routerId and lsa.age == 1800:
                            self.renew_lsa(lsa)
            time.sleep(1)

    def get_dbs(self):
        return self.dbs

    def set_dijkstra_manager(self, dijkstra):
        self.routeManager = dijkstra

    def set_interarea(self, isInterArea):
        self.isInterArea = isInterArea
        for intf in self.interfaceList:
            intf.isAbr = isInterArea
        self.routeManager.update_self_node_is_inter_area(self.ospfDb.routerId, isInterArea)
        idx = self.update_self_router_is_inter_area(isInterArea)
        for intf in self.interfaceList:
            intf.updateManager.send_multicast_update(intf, [idx])

    def get_interface_for_neighbor(self, neighborId):
        for intf in self.interfaceList:
            if intf.is_neighbor(neighborId):
                return intf
        return None

    def send_updates(self, updateList):
        for intf in self.interfaceList:
            intf.updateManager.send_multicast_update(intf, updateList)

    def add_interface(self, intf):
        self.lsdb_lock.acquire(True)
        self.interfaceList.append(intf)
        self.lsdb_lock.release()

    def remove_interface(self, intf):
        self.lsdb_lock.acquire(True)
        self.interfaceList.remove(intf)
        self.lsdb_lock.release()

    def get_lsa(self, idx):
        table = int(idx.split('-')[0])
        db = self.dbs[table]
        lock = self.locks[table]
        lock.acquire()
        lsa = db[idx]
        lock.release()
        return lsa

    def get_router_lsid(self):
        self.lsdb_lock.acquire(True)
        lsid = self.routerLSid
        # self.routerLSid += 1
        self.lsdb_lock.release()
        return lsid

    def kill_lsa(self, idx):
        lsType = int(idx.split('-')[0])
        table = self.dbs[lsType]
        lsa = table[idx]
        lsa.kill_lsa()

    def delete_lsa(self, db, key):
        lsa = db[key]
        lsa.process_delete(self)
        if key.split('-')[0] != '8':  # linkLSAs don't go to deadLSAs
            self.deadLSAs[key] = lsa
        del db[key]

    def set_router_bits(self):  # flags: V = 0; E = 0; B = ABR; x = 0; Nt = 0
        if self.ospfDb.router_is_inter_area():
            flags = 0x04
        else:
            flags = 0x00
        return flags

    def send_kill_updates(self, list):
        for intf in self.interfaceList:
            intf.updateManager.send_multicast_kill_lsa(intf, list)

    def get_inter_area_lsid(self, prefix):
        self.lsdb_lock.acquire(True)
        if prefix not in self.interAreaPrefixLsid.keys():
            lsid = '0.0.0.' + str(self.interAreaLSid)
            self.interAreaPrefixLsid[prefix] = lsid
            self.interAreaLSid += 1
        else:
            lsid = self.interAreaPrefixLsid[prefix]
        self.lsdb_lock.release()
        return lsid

    def clear_inter_area_lsas(self):
        list = []
        for lsid in self.interAreaPrefixLsid.values():
            idx = id(INTER_AREA_PREFIX_LSA, self.ospfDb.routerId, lsid)
            self.kill_lsa(idx)
            list.append(idx)
        self.interAreaPrefixLsid = {}
        self.send_kill_updates(list)

    def get_intra_area_lsid(self, refType, interface):
        if refType == NETWORK_LSA:
            lsid = '0.0.0.' + str(interface.intfNumber)
        else:
            lsid = self.ospfDb.routerId
        return lsid

    def ls_request(self, rid, lsType, advRouter, lsID, seq):
        idx = id(lsType, advRouter, lsID)
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

    def renew_lsa(self, lsa):
        lsa.update()
        self.update_area_interfaces(lsa.idx)

    def create_router_lsa(self, interface):
        lsid = '0.0.0.' + str(self.get_router_lsid())
        options = self.ospfDb.get_router_options()
        idx = id(ROUTER_LSA, self.ospfDb.routerId, lsid)
        if idx not in self.routerLS.keys():
            if interface.get_br_state():
                isInterArea = int(0x01)
            else:
                isInterArea = int(0x00)
            lsa = RouterLSA(self.ospfDb.routerId, 0, SEQUENCE_NUMBER, isInterArea, 0, {}, options, lsid)
            self.routerLS[idx] = lsa
        return idx

    def update_self_router_lsa_add_interface(self, interface):
        idx = id(int(ROUTER_LSA), self.ospfDb.routerId, '0.0.0.0')
        lsa = self.routerLS[idx]
        lsa.add_interface(interface)
        lsa.update()
        self.routerLS[idx] = lsa
        self.routeManager.update_node_add_interface(self.ospfDb.routerId, interface.intfId,
                                                    interface.interfaceOutputCost)
        if interface.designatedRouter != '0.0.0.0':
            neighbor = interface.designatedRouter + '.' + str(interface.designatedRouterIntf)
            self.routeManager.update_node_add_neighbor(self.ospfDb.routerId, neighbor, interface.interfaceOutputCost)
        return idx

    def update_self_router_lsa_remove_interface(self, interface):
        idx = id(int(ROUTER_LSA), self.ospfDb.routerId, '0.0.0.0')
        lsa = self.routerLS[idx]
        lsa.remove_interface(interface)
        lsa.update()
        self.routerLS[idx] = lsa
        self.routeManager.update_node_remove_interface(self.ospfDb.routerId, interface.intfId)
        if interface.designatedRouter != '0.0.0.0':
            neighbor = interface.designatedRouter + '.' + interface.designatedRouterIntf
            self.routeManager.update_node_remove_neighbor(self.ospfDb.routerId, neighbor)
        return idx

    def update_self_router_lsa_update_dr(self, interface):
        idx = id(int(ROUTER_LSA), self.ospfDb.routerId, '0.0.0.0')
        lsa = self.routerLS[idx]
        lsa.update_dr(interface)
        lsa.update()
        self.routerLS[idx] = lsa
        neighbor = interface.designatedRouter + '.' + interface.designatedRouterIntf
        self.routeManager.update_node_add_neighbor(self.ospfDb.routerId, neighbor, interface.interfaceOutputCost)
        return idx

    def update_self_router_lsa_custom_seq(self, newSeq):
        idx = id(int(ROUTER_LSA), self.ospfDb.routerId, '0.0.0.0')
        lsa = self.routerLS[idx]
        lsa.set_sequence_number(newSeq)
        lsa.update()
        return idx

    def update_self_router_lsa_update_cost(self, interface):
        self.router_lsa_lock.acquire()
        idx = id(int(ROUTER_LSA), self.ospfDb.routerId, '0.0.0.0')
        lsa = self.routerLS[idx]
        lsa.update_interface_cost(interface)
        lsa.update()
        self.routerLS[idx] = lsa
        self.router_lsa_lock.release()
        self.routeManager.update_node_change_interface_cost(self.ospfDb.routerId, interface.intfId,
                                                            interface.interfaceOutputCost)
        if interface.designatedRouter != '0.0.0.0':
            neighbor = interface.designatedRouter + '.' + interface.designatedRouterIntf
            self.routeManager.edit_cost(self.ospfDb.routerId, neighbor, interface.interfaceOutputCost)
        return idx

    def update_self_router_is_inter_area(self, isInterArea):
        self.router_lsa_lock.acquire()
        idx = id(int(ROUTER_LSA), self.ospfDb.routerId, '0.0.0.0')
        lsa = self.routerLS[idx]
        lsa.update_flags(isInterArea)
        lsa.update()
        self.router_lsa_lock.release()
        return idx

    def update_router_lsa(self, advRouter, age, seq, brState, links, options, lsid, dead):
        idx = id(int(ROUTER_LSA), advRouter, lsid)
        oldLSA = None
        try:
            self.router_lsa_lock.acquire()
            oldLSA = self.routerLS[idx]
            if oldLSA.sequenceNumber >= seq:
                self.router_lsa_lock.release()
                return
            else:
                self.router_lsa_lock.release()
        except:
            self.router_lsa_lock.release()
        lsa = RouterLSA(advRouter, age, seq, brState, 0, links, options, lsid)
        if not dead:
            self.router_lsa_lock.acquire()
            self.routerLS[idx] = lsa
            self.router_lsa_lock.release()
        else:
            self.router_lsa_lock.acquire()
            self.routerLS[idx] = lsa
            self.router_lsa_lock.release()
            return
        if brState == 0:
            isInterArea = False
        else:
            isInterArea = True
        if oldLSA is None:  # create new nodes and edges
            self.routeManager.add_node(ROUTER_LSA, advRouter, [], idx, isInterArea)
            if len(links) > 0:
                for link in links.values():
                    self.routeManager.update_node_add_interface(advRouter, link.interfaceId, link.metric)
                    if link.neighborRouterId != '0.0.0.0':
                        neighbor = link.neighborRouterId + '.' + '0.0.0.' + str(link.neighborInterfaceId)
                        self.routeManager.update_node_add_neighbor(advRouter, neighbor, link.metric)
        else:
            if oldLSA.flags != brState:
                self.routeManager.update_self_node_is_inter_area(advRouter, isInterArea)
            oldInterfaces = set(oldLSA.linkCount.keys())
            newInterfaces = set(links.keys())
            for intf in newInterfaces - oldInterfaces:  # new interfaces to add
                intf = links[intf]
                self.routeManager.update_node_add_interface(advRouter, intf.interfaceId, 0)
                if intf.neighborRouterId != '0.0.0.0':
                    neighbor = intf.neighborRouterId + '.' + '0.0.0.' + str(intf.neighborInterfaceId)
                    self.routeManager.update_node_add_neighbor(advRouter, neighbor, intf.metric)
            for intf in oldInterfaces.intersection(newInterfaces):  # updated interfaces
                newIntf = links[intf]
                oldIntf = oldLSA.linkCount[intf]
                if newIntf.neighborRouterId != oldIntf.neighborRouterId:
                    neighbor = newIntf.neighborRouterId + '.' + '0.0.0.' + str(newIntf.neighborInterfaceId)
                    self.routeManager.update_node_add_neighbor(advRouter, neighbor, newIntf.metric)
                if newIntf.metric != oldIntf.metric:
                    self.routeManager.update_node_change_interface_cost(advRouter, newIntf.interfaceId, newIntf.metric)
                    neighbor = newIntf.neighborRouterId + '.' + '0.0.0.' + str(newIntf.neighborInterfaceId)
                    self.routeManager.edit_cost(advRouter, neighbor, newIntf.metric)
            for intf in oldInterfaces - newInterfaces:  # old interfaces to remove
                intf = oldLSA.linkCount[intf]
                self.routeManager.update_node_remove_interface(advRouter, intf.interfaceId)
                if intf.neighborRouterId != '0.0.0.0':
                    neighbor = intf.neighborRouterId + '.0.0.0.' + str(intf.neighborInterfaceId)
                    self.routeManager.update_node_remove_neighbor(advRouter, neighbor)

    def create_network_lsa(self, interface):
        lsid = '0.0.0.' + str(interface.intfNumber)
        options = self.get_router_lsid()
        routerList = interface.neighborList.keys()
        routerList.append(self.ospfDb.routerId)
        lsa = NetworkLSA(self.ospfDb.routerId, 0, SEQUENCE_NUMBER, routerList, options, lsid)
        idx = id(int(NETWORK_LSA), self.ospfDb.routerId, lsid)
        self.networkLS[idx] = lsa
        netIdx = self.ospfDb.routerId + '.' + lsid
        self.routeManager.add_node(NETWORK_LSA, netIdx, [], idx, False)
        for neighbor in lsa.routerList:
            self.routeManager.update_node_add_neighbor(netIdx, neighbor, 0)
            if neighbor != self.ospfDb.routerId:
                self.routeManager.add_adjacency(netIdx, interface.intfId, interface.address)
        return idx

    def update_self_network_lsa(self, interface, neighborId):
        lsid = '0.0.0.' + str(interface.intfNumber)
        idx = id(int(NETWORK_LSA), self.ospfDb.routerId, lsid)
        lsa = self.networkLS[idx]
        netIdx = self.ospfDb.routerId + '.' + lsid
        if neighborId in lsa.routerList:
            lsa.routerList.remove(neighborId)
            self.routeManager.update_node_remove_neighbor(netIdx, neighborId)
        else:
            lsa.routerList.append(neighborId)
            self.routeManager.update_node_add_neighbor(netIdx, neighborId, 0)
            self.routeManager.add_adjacency(netIdx, interface.intfId, interface.address)
        lsa.update()
        return idx

    def update_network_lsa(self, advRouter, lsid, age, seq, options, routerList, dead):
        idx = id(int(NETWORK_LSA), advRouter, lsid)
        oldLSA = None
        try:
            oldLSA = self.networkLS[idx]
            if oldLSA.sequenceNumber >= seq:
                return
        except:
            pass
        lsa = NetworkLSA(advRouter, age, seq, routerList, options, lsid)
        if not dead:
            self.network_lsa_lock.acquire()
            self.networkLS[idx] = lsa
            self.network_lsa_lock.release()
        else:
            self.network_lsa_lock.acquire()
            self.networkLS[idx] = lsa
            self.network_lsa_lock.release()
            return
        netIdx = advRouter + '.' + lsid
        if oldLSA is None:
            self.routeManager.add_node(NETWORK_LSA, netIdx, [], idx, False)
            for neighbor in lsa.routerList:
                self.routeManager.update_node_add_neighbor(netIdx, neighbor, 0)
            if self.ospfDb.routerId in lsa.routerList:
                for router in lsa.routerList:
                    if router != self.ospfDb.routerId:
                        interface = self.get_interface_for_neighbor(router)
                        self.routeManager.add_adjacency(netIdx, interface.intfId, interface.address)
        else:
            oldNeighbors = set(oldLSA.routerList)
            newNeighbors = set(lsa.routerList)
            for neighbor in newNeighbors - oldNeighbors:  # neighbors to add
                self.routeManager.update_node_add_neighbor(netIdx, neighbor, 0)
                interface = self.get_interface_for_neighbor(neighbor)
                self.routeManager.add_adjacency(netIdx, interface.intfId, interface.address)
            for neighbor in oldNeighbors - newNeighbors:  # neighbors to remove
                self.routeManager.update_node_remove_neighbor(netIdx, neighbor)

    def kill_self_network_lsa(self, interface):
        lsid = '0.0.0.' + str(interface.intfNumber)
        idx = id(int(NETWORK_LSA), self.ospfDb.routerId, lsid)
        self.kill_lsa(idx)
        return idx

    def create_inter_area_prefix_lsa(self, metric, prefixLength, prefixOptions, prefix):
        lsid = self.get_inter_area_lsid(prefix)
        idx = id(int(INTER_AREA_PREFIX_LSA), self.ospfDb.routerId, lsid)
        lsa = InterAreaPrefixLSA(self.ospfDb.routerId, 0, SEQUENCE_NUMBER, metric, prefixLength, prefixOptions, prefix,
                                 lsid)
        self.interAreaPrefixLS[idx] = lsa
        return idx

    def update_self_inter_area_prefix_lsas(self, newPrefixes, oldPrefixes):
        updates = []    # [idx1, idx2, ...]
        for prefix in newPrefixes.values():
            if prefix.address not in self.interAreaPrefixLsid.keys():
                idx = self.create_inter_area_prefix_lsa(prefix.metric, prefix.length, prefix.options, prefix.address)
                updates.append(idx)
                self.routeManager.add_inter_area_prefix(self.ospfDb.routerId, deepcopy(prefix))
            else:
                idx = id(INTER_AREA_PREFIX_LSA, self.ospfDb.routerId, self.get_inter_area_lsid(prefix.address))
                lsa = self.interAreaPrefixLS[idx]
                if lsa.prefix.update_metric(prefix.metric):
                    lsa.update()
                    updates.append(idx)
                    self.routeManager.add_inter_area_prefix(self.ospfDb.routerId, deepcopy(prefix))
        for prefix in set(oldPrefixes.keys()) - set(newPrefixes.keys()):
            prefix = oldPrefixes[prefix]
            idx = self.kill_self_inter_area_prefix_lsa(prefix)
            updates.append(idx)
            self.routeManager.remove_inter_area_prefix(self.ospfDb.routerId, prefix.get_full_address())
        if len(updates) > 0:
            self.send_updates(updates)

    def kill_self_inter_area_prefix_lsa(self, prefix):
        lsid = self.get_inter_area_lsid(prefix.address)
        idx = id(int(INTER_AREA_PREFIX_LSA), self.ospfDb.routerId, lsid)
        lsa = self.get_lsa(idx)
        self.routeManager.remove_inter_area_prefix(self.ospfDb.routerId, lsa.prefix.get_full_address())
        self.kill_lsa(idx)
        del self.interAreaPrefixLsid[prefix.address]
        return idx

    def update_inter_area_prefix_lsa(self, advRouter, age, seq, metric, prefixLength, prefixOptions, prefix, lsid,
                                     dead):
        idx = id(int(INTER_AREA_PREFIX_LSA), advRouter, lsid)
        try:
            oldLsa = self.interAreaPrefixLS[idx]
            if oldLsa.sequenceNumber >= seq:
                return
        except:
            oldLsa = None
        lsa = InterAreaPrefixLSA(advRouter, age, seq, metric, prefixLength, prefixOptions, prefix, lsid)
        if not dead:
            self.interarea_lsa_lock.acquire()
            self.interAreaPrefixLS[idx] = lsa
            self.interarea_lsa_lock.release()
            self.routeManager.add_inter_area_prefix(advRouter, lsa.prefix)
        else:
            self.interarea_lsa_lock.acquire()
            self.interAreaPrefixLS[idx] = lsa
            self.interarea_lsa_lock.release()
            self.routeManager.remove_inter_area_prefix(advRouter, oldLsa.prefix.get_full_address())
            return

    def create_link_lsa(self, interface):
        lsid = '0.0.0.' + str(interface.intfNumber)
        idx = id(LINK_LSA, self.ospfDb.routerId, lsid)
        options = self.ospfDb.get_router_options()
        if interface.fullAddress is None:
            lsa = LinkLSA(self.ospfDb.routerId, 0, SEQUENCE_NUMBER, lsid, interface.routerPriority, options,
                          interface.address, None, interface.intfId)
        else:
            addr, length = interface.fullAddress.split('/')
            if length < 128:
                prefixOptions = int(0x00)
            else:
                prefixOptions = int(0x02)
            newPrefix = Prefix(addr, length, 0, prefixOptions)
            lsa = LinkLSA(self.ospfDb.routerId, 0, SEQUENCE_NUMBER, lsid, interface.routerPriority, options,
                          interface.address, {addr: newPrefix}, interface.intfId)
        self.linkLS[idx] = lsa

    def update_self_link_lsa(self, interface, linkLocal, prefix):
        lsid = '0.0.0.' + str(interface.intfNumber)
        idx = id(LINK_LSA, self.ospfDb.routerId, lsid)
        lsa = self.linkLS[idx]
        lsa.address = linkLocal
        addr, length = prefix.split('/')
        if int(length) < 128:
            addr = addressManagment.trim_ip(addr, length)
            options = int(0x00)
        else:
            options = int(0x02)
        if addr in lsa.prefixList:
            lsa.remove_prefix(addr)
        else:
            lsa.add_prefix(addr, Prefix(addr, length, 0, options))
        lsa.update()
        return idx

    def update_link_lsa(self, advRouter, age, seq, lsid, priority, options, address, prefixList, interfaceId, dead):
        idx = id(LINK_LSA, advRouter, lsid)
        try:
            lsa = self.linkLS[idx]
            if lsa.sequenceNumber >= seq:
                return
        except:
            pass
        lsa = LinkLSA(advRouter, age, seq, lsid, priority, options, address, prefixList, interfaceId)
        self.routeManager.add_adjacency(advRouter, interfaceId, address)
        try:
            oldLsa = self.linkLS[idx]
        except:
            oldLsa = None
        if not dead:
            self.link_lsa_lock.acquire()
            self.linkLS[idx] = lsa
            self.link_lsa_lock.release()
        else:
            self.link_lsa_lock.acquire()
            self.linkLS[idx] = lsa
            self.link_lsa_lock.release()
            return
        intf = self.ospfDb.interfaceList[interfaceId]
        if intf.state == interface.STATES['DR'] and prefixList is not None:
            kill = False
            idx1 = None
            idx2 = None
            if oldLsa is not None:
                oldPrefixes = set(oldLsa.prefixList.keys())
                newPrefixes = set(prefixList.keys())
                prefixesToAdd = newPrefixes - oldPrefixes
                prefixesToDelete = oldPrefixes - newPrefixes
            else:
                prefixesToAdd = prefixList.keys()
                prefixesToDelete = None
            prefixes = []
            for prefix in prefixesToAdd:
                if int(prefixList[prefix].length) < 128:
                    prefix = deepcopy(prefixList[prefix])
                    prefix.metric = intf.interfaceOutputCost
                    prefixes.append(prefix)
            if len(prefixes) > 0:
                reflsid = '0.0.0.' + str(intf.intfNumber)
                idx1 = self.create_intra_area_prefix_lsa(intf, NETWORK_LSA, reflsid, prefixes, intf.routerId)
                if idx1 is None:
                    idx1 = self.update_self_intra_area_prefix_lsa_add(NETWORK_LSA, intf, prefixes)
            prefixes = []
            if prefixesToDelete is not None:
                for prefix in prefixesToDelete:
                    if int(oldLsa.prefixList[prefix].length) < 128:
                        prefixes.append(oldLsa.prefixList[prefix])
                if len(prefixes) > 0:
                    idx2, kill = self.update_self_intra_area_prefix_lsa_remove(NETWORK_LSA, intf, prefixes)
            if idx1 is not None or (idx2 is not None and not kill):
                idx = idx1 or idx2
                self.update_area_interfaces(idx)
            if kill:
                for intf in self.interfaceList:
                    intf.updateManager.send_multicast_kill_lsa(idx2)

    def kill_self_link_lsa(self, interface):
        lsid = '0.0.0.' + str(interface.intfNumber)
        idx = id(int(LINK_LSA), self.ospfDb.routerId, lsid)
        self.kill_lsa(idx)

    def create_intra_area_prefix_lsa(self, interface, refLSType, refLSId, prefixes,
                                     refAdvRouter):  # prefixes == [Prefix(),]
        lsid = self.get_intra_area_lsid(refLSType, interface)
        idx = id(INTRA_AREA_PREFIX_LSA, self.ospfDb.routerId, lsid)
        prefixList = {}
        if idx in self.intraAreaLS.keys():  # if lsa already exists return None and go for update instead
            return None
        else:
            if type(refLSId) is int:
                refLSId = '0.0.0.' + str(refLSId)
        for prefix in prefixes:
            prefixList[prefix.address] = prefix
        lsa = IntraAreaPrefixLSA(self.ospfDb.routerId, 0, SEQUENCE_NUMBER, lsid, refLSType, refLSId, prefixList,
                                 refAdvRouter)
        self.intraarea_lsa_lock.acquire()
        self.intraAreaLS[idx] = lsa
        self.intraarea_lsa_lock.release()
        if refLSType == ROUTER_LSA:
            nodeId = self.ospfDb.routerId
        else:
            nodeId = refAdvRouter + '.' + refLSId
        for prefix in prefixes:
            self.routeManager.add_prefix(nodeId, prefix)
        return idx

    def update_self_intra_area_prefix_lsa_add(self, refLSType, interface, prefixes):  # prefixes == [Prefix(),]
        lsid = self.get_intra_area_lsid(refLSType, interface)
        idx = id(INTRA_AREA_PREFIX_LSA, self.ospfDb.routerId, lsid)
        update = False
        self.intraarea_lsa_lock.acquire()
        lsa = self.intraAreaLS[idx]
        prefixList = []
        for prefix in prefixes:
            if prefix.address not in lsa.prefixList.keys():
                lsa.add_prefix(prefix.address, prefix)
                prefixList.append(prefix)
                update = True
            else:
                if prefix.metric != lsa.prefixList[prefix.address].metric:
                    lsa.update_metric(idx, prefix.metric)
                    lsa.prefixList[prefix.address].update_metric(prefix.metric)
                    update = True
        self.intraarea_lsa_lock.release()
        if refLSType == ROUTER_LSA:
            nodeId = self.ospfDb.routerId
        else:
            nodeId = self.ospfDb.routerId + '.0.0.0.' + str(interface.intfNumber)
        for prefix in prefixList:
            self.routeManager.add_prefix(nodeId, prefix)
        if update:
            return idx
        else:
            return None

    def update_self_intra_area_prefix_lsa_remove(self, refLSType, interface, prefixes):  # prefixes == [addr,]
        kill = False
        update = False
        lsid = self.get_intra_area_lsid(refLSType, interface)
        idx = id(int(INTRA_AREA_PREFIX_LSA), self.ospfDb.routerId, lsid)
        self.intraarea_lsa_lock.acquire()
        lsa = self.intraAreaLS[idx]
        prefixList = []
        for prefix in prefixes:
            try:
                lsa.remove_prefix(prefix)
                prefixList.append(prefix)
                update = True
            except:
                pass
        self.intraarea_lsa_lock.release()
        if len(lsa.prefixList.keys()) == 0:
            self.kill_lsa(idx)
            kill = True
        if update:
            if not kill:
                lsa.update()
                lsa.update()
                if refLSType == ROUTER_LSA:
                    nodeId = self.ospfDb.routerId
                else:
                    nodeId = self.ospfDb.routerId + '.0.0.0.' + str(interface.intfNumber)
                for prefix in prefixList:
                    self.routeManager.remove_prefix(nodeId, prefix)
            return idx, kill
        else:
            return None, kill

    def update_intra_area_prefix_lsa(self, advRouter, lsid, seq, age, refLSType, refLSId, prefixList, refAdvRouter,
                                     dead):
        idx = id(int(INTRA_AREA_PREFIX_LSA), advRouter, lsid)
        oldLSA = None
        try:
            oldLSA = self.intraAreaLS[idx]
            if oldLSA.sequenceNumber >= seq:
                return
        except:
            pass
        lsa = IntraAreaPrefixLSA(advRouter, age, seq, lsid, refLSType, refLSId, prefixList, refAdvRouter)
        if not dead:
            self.intraarea_lsa_lock.acquire()
            self.intraAreaLS[idx] = lsa
            self.intraarea_lsa_lock.release()
        else:
            self.intraarea_lsa_lock.acquire()
            self.intraAreaLS[idx] = lsa
            self.intraarea_lsa_lock.release()
            return
        if refLSType == ROUTER_LSA:
            nodeId = refAdvRouter
        else:
            nodeId = refAdvRouter + '.' + refLSId
        if oldLSA is None:
            for prefix in prefixList.values():
                self.routeManager.add_prefix(nodeId, prefix)
        else:
            oldPrefixes = set(oldLSA.prefixList.values())
            newPrefixes = set(lsa.prefixList.values())
            for prefix in newPrefixes - oldPrefixes:  # new prefixes to add
                self.routeManager.add_prefix(nodeId, prefix)
            for prefix in oldPrefixes - newPrefixes:  # old prefixes to remove
                self.routeManager.remove_prefix(nodeId, prefix)

    def kill_self_intra_area_prefix_lsa_router(self, interface):
        lsid = self.get_intra_area_lsid(ROUTER_LSA, interface)
        idx = id(INTRA_AREA_PREFIX_LSA, self.ospfDb.routerId, lsid)
        if idx in self.intraAreaLS.keys():
            self.kill_lsa(idx)
            return idx
        else:
            return None

    def kill_self_intra_area_prefix_lsa_network(self, interface):
        lsid = self.get_intra_area_lsid(NETWORK_LSA, interface)
        idx = id(INTRA_AREA_PREFIX_LSA, self.ospfDb.routerId, lsid)
        if idx in self.intraAreaLS.keys():
            self.kill_lsa(idx)
            return idx
        else:
            return None

    def update_area_interfaces(self, idx):
        if type(idx) is not list:
            idx = [idx]
        for intf in self.interfaceList:
            intf.updateManager.send_multicast_update(intf, idx)

    def print_lsdb(self):
        print(
        '\t\t\tOSPF Router ID (' + self.ospfDb.routerId + ') (Process ID ' + str(self.ospfDb.ospf_process_id) + ')')
        if len(self.routerLS) > 0:
            print('\n\t\t\t\tRouter Link States (Area ' + self.area + ')')
            print('ADV Router\t\tAge\t\tSeq#\t\t\tFragment ID\t\tLink count\t\tBits')
            for lsa in sorted(self.routerLS.keys()):
                self.routerLS[lsa].print_lsa()
        if len(self.networkLS) > 0:
            print('\n\t\t\t\tNetwork Link States (Area ' + self.area + ')')
            print('ADV Router\t\tAge\t\tSeq#\t\t\tLink ID\t\tRtr count')
            for lsa in sorted(self.networkLS.keys()):
                self.networkLS[lsa].print_lsa()
        if len(self.interAreaPrefixLS) > 0:
            print('\n\t\t\t\tInter Area Prefix Link States (Area ' + self.area + ')')
            print('ADV Router\t\tAge\t\t\tSeq#\t\t#Prefixes')
            for lsa in sorted(self.interAreaPrefixLS.keys()):
                self.interAreaPrefixLS[lsa].print_lsa()
        if len(self.linkLS) > 0:
            print('\n\t\t\t\tLink (Type-?) Link States (Area ' + self.area + ')')
            print('ADV Router\t\tAge\t\tSeq#\t\t\tLink ID\t\tInterface')
            for lsa in sorted(self.linkLS.keys()):
                self.linkLS[lsa].print_lsa()
        if len(self.intraAreaLS) > 0:
            print('\n\t\t\t\tIntra Area Prefix States (Area ' + self.area + ')')
            print('ADV Router\t\tAge\t\t\tSeq#\t\tLink ID\t\tRef-lstype\t\tRef-LSID\t\t#Prefixes')
            for lsa in sorted(self.intraAreaLS.keys()):
                self.intraAreaLS[lsa].print_lsa()

    def print_lsas(self, type):  # type = LS_Type
        table = self.dbs[type]
        if table:
            for lsa in table.values():
                lsa.print_lsa_detailed()


class LSA:
    def __init__(self, lsType, advRouter, age, sequenceNumber):
        self.lsType = lsType
        self.advRouter = advRouter
        self.sequenceNumber = sequenceNumber
        self.age = age
        self.lsid = None
        self.idx = None
        self.lsaHeader = None

        self.lsaLock = threading.Lock()

    def lock_lsa(self):
        self.lsaLock.acquire()

    def unlock_lsa(self):
        self.lsaLock.release()

    def set_sequence_number(self, newseq):
        self.lock_lsa()
        self.sequenceNumber = newseq
        self.age = 0
        self.unlock_lsa()

    def update(self):
        self.lock_lsa()
        self.sequenceNumber += 1
        self.age = 0
        self.unlock_lsa()

    def package_lsa(self, isfull):
        self.lsaHeader = packetManager.LSAHeader(self.age, self.lsType, self.lsid, self.advRouter, self.sequenceNumber)

    def print_lsa(self):
        pass

    def print_lsa_detailed(self):
        print('\tAdv Router: ' + self.advRouter)
        print('\tSequence Number: ' + hex(self.sequenceNumber))
        print('\tAge: ' + str(self.age))
        print('\tLink State ID: ' + str(self.lsid))

    def kill_lsa(self):
        self.age = 3600
        self.sequenceNumber += 1

    def process_delete(self, db):
        pass


class RouterLSA(LSA):
    def __init__(self, advRouter, age, sequenceNumber, brState, fragmentId, linkCount, options, lsid):
        LSA.__init__(self, ROUTER_LSA, advRouter, age, sequenceNumber)
        self.fragmentId = fragmentId  # 0
        self.linkCount = linkCount  # {interface.intfNumber: RouterLink}
        self.flags = brState  # 0x01 v 0x00
        self.options = options  # 0x33
        self.lsid = lsid  # '0.0.0.' + str(routerLSid)
        self.order = 0

        self.idx = id(self.lsType, advRouter, self.lsid)

    def get_order(self):
        self.order += 1
        return self.order - 1

    def add_interface(self, interface):
        if interface.is_dr():
            link = RouterLink(interface.interfaceOutputCost, interface.intfNumber, interface.intfNumber,
                              interface.ospfDb.routerId, self.get_order())
        else:
            neighborRouterId = interface.designatedRouter
            neighborInterfaceId = interface.neighborList[neighborRouterId].neighborIntfId
            link = RouterLink(interface.interfaceOutputCost, interface.intfNumber, neighborInterfaceId, neighborRouterId, self.get_order())
        self.lock_lsa()
        self.linkCount[interface.intfNumber] = link
        self.unlock_lsa()

    def remove_interface(self, interface):
        self.lock_lsa()
        del self.linkCount[interface.intfNumber]
        self.unlock_lsa()

    def update_dr(self, interface):
        self.lock_lsa()
        link = self.linkCount[interface.intfNumber]
        drId = interface.designatedRouter
        try:
            drintfId = interface.neighborList[drId].neighborIntfId
        except:
            drintfId = interface.intfNumber
        link.update_dr(drintfId, drId)
        self.unlock_lsa()

    def update_interface_cost(self, interface):
        self.lock_lsa()
        link = self.linkCount[interface.intfNumber]
        link.update_metric(interface.interfaceOutputCost)
        self.unlock_lsa()

    def get_flags(self):  # output (W,V,E,B)
        if self.flags == int(0x01):
            out = (0, 0, 0, 1)
        else:
            out = (0, 0, 0, 0)
        return out

    def update_flags(self, brState):
        aux = {True: int(0x01), False: int(0x00)}
        if aux[brState] == self.flags:
            return False
        else:
            self.flags = aux[brState]
            return True

    def process_delete(self, lsdb):
        nodeId = self.advRouter
        lsdb.routeManager.remove_node(nodeId)

    def package_lsa(self, isfull):
        LSA.package_lsa(self, isfull)
        lsa = packetManager.LSAType1(self.lsaHeader)
        return lsa.build(self, isfull)

    def print_lsa(self):
        self.lock_lsa()
        LSA.print_lsa(self)
        print(
        self.advRouter + '\t\t\t' + str(self.age) + '\t\t' + hex(self.sequenceNumber) + '\t\t' + str(self.fragmentId) +
        '\t\t\t\t' + str(len(self.linkCount)))
        self.unlock_lsa()

    def print_lsa_detailed(self):
        self.lock_lsa()
        print('\nRouter LSA ' + self.advRouter + '\n')
        LSA.print_lsa_detailed(self)
        print('\tFragment ID: ' + str(self.fragmentId))
        print('\tLinks: ')
        self.print_links()
        print('\tFlags: ' + str(self.flags))
        print('\tOptions: (' + read_Options(self.options) + ')')
        self.unlock_lsa()

    def print_links(self):
        for link in self.linkCount.values():
            link.print_link()


class RouterLink:
    def __init__(self, metric, interfaceId, neighborInterfaceId, neighborRouterId, order):
        self.type = 2
        self.metric = metric
        self.interfaceId = interfaceId
        self.neighborInterfaceId = neighborInterfaceId
        self.neighborRouterId = neighborRouterId
        self.order = int(order)

    def update_metric(self, newMetric):
        self.metric = newMetric

    def update_dr(self, drInterfaceId, drRouterId):
        self.neighborInterfaceId = drInterfaceId
        self.neighborRouterId = drRouterId

    def print_link(self):
        print('\n\t\tLink Type: ' + str(self.type))
        print('\t\tMetric: ' + str(self.metric))
        print('\t\tInterface ID: ' + str(self.interfaceId))
        print('\t\tNeighbor Interface ID: ' + str(self.neighborInterfaceId))
        print('\t\tNeighbor Router ID: ' + self.neighborRouterId)


class NetworkLSA(LSA):
    def __init__(self, advRouter, age, sequenceNumber, routerList, options, lsid):
        LSA.__init__(self, NETWORK_LSA, advRouter, age, sequenceNumber)
        self.routerList = routerList  # [neighbor1, neighbor2]
        self.options = options
        self.lsid = lsid
        self.idx = id(self.lsType, advRouter, self.lsid)

    def package_lsa(self, isfull):
        LSA.package_lsa(self, isfull)
        lsa = packetManager.LSAType2(self.lsaHeader)
        return lsa.build(self, isfull)

    def process_delete(self, lsdb):
        nodeId = self.advRouter + '.' + self.lsid
        for neighbor in self.routerList:
            try:
                lsdb.routeManager.remove_edge(neighbor, nodeId)
            except:
                pass
        lsdb.routeManager.remove_node(nodeId)

    def print_lsa(self):
        self.lock_lsa()
        LSA.print_lsa(self)
        print(self.advRouter + '\t\t\t' + str(self.age) + '\t\t' + hex(self.sequenceNumber) + '\t\t' + str(self.lsid) +
              '\t\t\t\t' + str(len(self.routerList)))
        self.unlock_lsa()

    def print_lsa_detailed(self):
        self.lock_lsa()
        print('\tNetwork LSA ' + self.idx + '\n')
        LSA.print_lsa_detailed(self)
        print('\tNeighbors: ' + self.routerList[0] + ', ' + self.routerList[1])
        print('\tOptions: (' + read_Options(self.options) + ')')
        self.unlock_lsa()


class InterAreaPrefixLSA(LSA):
    def __init__(self, advRouter, age, sequenceNumber, metric, prefixLength, prefixOptions, prefix, lsId):
        LSA.__init__(self, INTER_AREA_PREFIX_LSA, advRouter, age, sequenceNumber)
        self.prefix = Prefix(prefix, prefixLength, metric, prefixOptions)
        self.lsid = lsId
        self.idx = id(self.lsType, advRouter, self.lsid)

    def update_metric(self, newMetric):
        self.lock_lsa()
        self.prefix.update_metric(newMetric)
        self.unlock_lsa()

    def package_lsa(self, isfull):
        LSA.package_lsa(self, isfull)
        lsa = packetManager.LSAType3(self.lsaHeader)
        return lsa.build(self, isfull)

    def print_lsa(self):
        self.lock_lsa()
        LSA.print_lsa(self)
        print(self.advRouter + '\t\t\t' + str(self.age) + '\t\t\t' + hex(self.sequenceNumber) + '\t' + str(self.lsid) +
              '\t\t' + str(self.prefix.address) + '/' + str(self.prefix.length))
        self.unlock_lsa()

    def print_lsa_detailed(self):
        self.lock_lsa()
        print('\nInter-Area-Prefix LSA ' + self.idx + '\n')
        LSA.print_lsa_detailed(self)
        self.prefix.print_prefix()
        self.unlock_lsa()


class IntraAreaPrefixLSA(LSA):
    def __init__(self, advRouter, age, sequenceNumber, lsid, refLStype, refLSid, prefixList, refAdvRouter):
        LSA.__init__(self, INTRA_AREA_PREFIX_LSA, advRouter, age, sequenceNumber)
        self.lsid = lsid  # 0.0.0.0
        self.refLsType = refLStype
        self.refLsId = refLSid
        self.prefixList = prefixList  # {prefix/length:Prefix}
        self.refAdvRouter = refAdvRouter

        self.idx = id(self.lsType, self.advRouter, self.lsid)

    def add_prefix(self, idx, prefix):
        self.lock_lsa()
        self.prefixList[idx] = prefix
        self.unlock_lsa()

    def remove_prefix(self, idx):
        self.lock_lsa()
        del self.prefixList[idx]
        self.unlock_lsa()

    def update_metric(self, idx, newMetric):
        self.lock_lsa()
        prefix = self.prefixList[idx]
        prefix.update_metric(newMetric)
        self.unlock_lsa()

    def process_delete(self, lsdb):
        if self.refLsType == ROUTER_LSA:
            nodeId = self.refAdvRouter
        else:
            nodeId = self.refAdvRouter + '.' + self.refLsId
        try:
            node = lsdb.routeManager.get_node(nodeId)
            for prefix in node.get_prefixes().values():
                lsdb.routeManager.remove_prefix(nodeId, prefix)
        except:
            pass

    def package_lsa(self, isfull):
        LSA.package_lsa(self, isfull)
        lsa = packetManager.LSAType9(self.lsaHeader)
        return lsa.build(self, isfull)

    def print_lsa(self):
        self.lock_lsa()
        LSA.print_lsa(self)
        print(self.advRouter + '\t\t\t' + str(self.age) + '\t\t\t' + hex(self.sequenceNumber) + '\t' + str(self.lsid) +
              '\t\t' + hex(self.refLsType) + '\t\t\t' + str(self.refLsId) + '\t\t' + str(len(self.prefixList)))
        self.unlock_lsa()

    def print_lsa_detailed(self):
        self.lock_lsa()
        print('\nIntra-Area-Prefix LSA ' + self.idx + '\n')
        LSA.print_lsa_detailed(self)
        print('\tReference LS Type: ' + hex(self.refLsType))
        print('\tReference LS ID: ' + self.lsid)
        self.print_prefixes()
        self.unlock_lsa()

    def print_prefixes(self):
        for prefix in self.prefixList.values():
            prefix.print_prefix()


class Prefix:
    def __init__(self, address, length, metric, options):
        self.address = address
        self.length = int(length)
        self.metric = int(metric)
        self.options = options

    def update_metric(self, newMetric):
        if newMetric != self.metric:
            self.metric = newMetric
            return True
        else:
            return False

    def get_full_address(self):
        return self.address + '/' + str(self.length)

    def print_prefix(self):
        print('\n\t\tPrefix Address: ' + self.address)
        print('\t\tPrefix Length: ' + str(self.length))
        print('\t\tOptions: ' + read_Options(self.options))
        print('\t\tMetric: ' + str(self.metric))


class LinkLSA(LSA):
    def __init__(self, advRouter, age, sequenceNumber, lsid, priority, options, address, prefixList, intfId):
        LSA.__init__(self, LINK_LSA, advRouter, age, sequenceNumber)
        self.lsid = lsid
        self.priority = priority
        self.options = options
        self.address = address
        self.interface = intfId
        if prefixList is None:
            self.numberPrefixes = 0
        else:
            self.numberPrefixes = len(prefixList)
        if prefixList is None:
            self.prefixList = {}
        else:
            self.prefixList = prefixList

        self.idx = id(LINK_LSA, advRouter, self.lsid)

    def update_priority(self, newPriority):
        self.lock_lsa()
        self.priority = newPriority
        self.unlock_lsa()

    def update_address(self, newAddress):
        self.lock_lsa()
        self.address = newAddress
        self.unlock_lsa()

    def add_prefix(self, idx, newPrefix):
        self.lock_lsa()
        self.prefixList[idx] = newPrefix
        self.numberPrefixes += 1
        self.unlock_lsa()

    def remove_prefix(self, idx):
        self.lock_lsa()
        del self.prefixList[idx]
        self.numberPrefixes -= 1
        self.unlock_lsa()

    def update_metric(self, idx, newMetric):
        self.lock_lsa()
        prefix = self.prefixList[idx]
        prefix.update_metric(newMetric)
        self.unlock_lsa()

    def package_lsa(self, isfull):
        LSA.package_lsa(self, isfull)
        lsa = packetManager.LSAType8(self.lsaHeader)
        return lsa.build(self, isfull)

    def print_lsa(self):
        self.lock_lsa()
        LSA.print_lsa(self)
        print(self.advRouter + '\t\t\t' + str(self.age) + '\t\t' + hex(self.sequenceNumber) + '\t\t' + str(
            self.lsid) + '\t\t' + str(self.interface))
        self.unlock_lsa()

    def print_lsa_detailed(self):
        self.lock_lsa()
        print('\nLink LSA ' + self.idx + '\n')
        LSA.print_lsa_detailed(self)
        print('\tPriority: ' + str(self.priority))
        print('\tOptions: ' + read_Options(self.options))
        print('\tAddress: ' + self.address)
        print('\t#Prefixes: ' + str(len(self.prefixList)))
        self.print_prefixes()
        self.unlock_lsa()

    def print_prefixes(self):
        for prefix in self.prefixList.values():
            prefix.print_prefix()


def id(lsType, advRouter, lsID):
    return str(lsType) + '-' + advRouter + '-' + str(lsID)


def read_Options(options):
    options = '{0:b}'.format(options)
    bits = ['AT', 'L', 'AF', 'DC', 'R', 'N', 'MC', 'E', 'V6']
    out = ''
    for i in range(len(options)):
        if options[i] == '1':
            out += bits[i] + '-bit  '
    return out