import packetManager
import threading
import time
import socket
import neighbors
import linkStateDatabase
import addressManagment


OSPF_GROUP_ADDRESS = 'ff02::5'
NEIGHBOR_STATE = ['Down', 'Attempt', 'Init', '2-Way', 'ExStart', 'ExChange', 'Loading', 'Full']
STATES = {'Down': 0,
          'Loopback': 1,
          'Waiting': 2,
          'Point-to-point': 3,
          'DR other': 4,
          'Backup': 5,
          'DR': 6}


class ActiveInterface:
    def __init__(self, intfId, areaId, ospfDb, isInterArea, cost, nbr):
        global waiting
        waiting = True
        self.active = True
        self.neighborList = {}  # {neighborID: neighbor object}
        self.adjacencies = []  # [neighborID,]
        self.type = 'broadcast'  # 'broadcast'
        self.state = STATES['Down']  # interface state
        self.intfId = intfId  # intf name 'ens33'
        self.address = addressManagment.get_interface_address(intfId)  # get link local address from interface
        self.fullAddress = None  # full address
        self.scopeId = addressManagment.get_int_scopeid(intfId)  # internal interface number
        self.intfNumber = nbr
        self.areaId = areaId  # Area ID
        self.helloInterval = 10  # default 10s
        self.routerDeadInterval = 4 * self.helloInterval  # default 4* hello = 40
        self.IntfTransDelay = 1000  # default for ethernet 1000
        self.routerPriority = 1  # default 1
        self.helloTimer = self.helloInterval  # same as Hello interval
        self.waitTimer = self.routerDeadInterval  # same as routerDeadInterval
        self.designatedRouter = '0.0.0.0'  # DR router ID for the link
        self.designatedRouterIntf = '0.0.0.0'
        self.backupDesignatedRouter = '0.0.0.0'  # BDR router ID for the link
        self.updateManager = UpdateManager(self)
        self.routerLsid = 0
        self.routerId = ospfDb.get_router_id()
        if cost == 0:
            self.interfaceOutputCost = 5
        else:
            self.interfaceOutputCost = int(cost)
        self.rxmtInterval = 5  # default

        self.interfaceLock = threading.Lock()

        self.hasNeighbor = False
        self.ospfDb = ospfDb
        self.interAreaPrefixes = {}
        self.intraAreaPrefixes = {}

        self.isAbr = isInterArea
        self.isAsbr = False

        self.update_lsdb()

        self.thread = threading.Thread(target=self.run, args=(ospfDb.addressList,))
        self.thread.daemon = True
        self.thread.start()

    def run(self, addrList):
        global waiting
        self.interface_up(addrList)
        Receiver(self)
        while self.active:
            self.helloTimer -= 1
            if self.helloTimer == 0:
                self.helloTimer = self.helloInterval
                SendHelloPacket(self)
            time.sleep(1)

    def get_lsdb(self):
        return self.ospfDb.get_lsdb(self.areaId)

    def clear_ip_config(self):
        if self.fullAddress is not None:
            addressManagment.clear_ip_config(self.intfId, self.fullAddress)

    def interface_up(self, addrList):
        global waiting
        if self.routerPriority == 0:
            self.state = STATES['DR other']
        else:
            self.state = STATES['Waiting']
            Waiting(self, addrList)
            while waiting:
                self.waitTimer -= 1
                if self.waitTimer == 0:
                    waiting = False
                else:
                    time.sleep(1)
            elect_dr_bdr(self, True)

    def get_interface_neighbors(self):
        neighbors = []
        for neighbor in self.neighborList.values():
            if neighbor.neighbor_is_full():
                neighbors.append(neighbor.neighborId)
        return neighbors

    def is_neighbor(self, neighborId):
        return neighborId in self.neighborList.keys()

    def add_inter_area_prefix_binding(self, lsid, prefix):
        self.interfaceLock.acquire()
        self.interAreaPrefixes[prefix] = lsid
        self.interfaceLock.release()

    def del_inter_area_prefix_binding(self, prefix):
        self.interfaceLock.acquire()
        del self.interAreaPrefixes[prefix]
        self.interfaceLock.release()

    def get_inter_area_prefix_lsid(self, prefix):
        self.interfaceLock.acquire()
        lsid = self.interAreaPrefixes[prefix]
        self.interfaceLock.release()
        return lsid

    def add_intra_area_prefix_binding(self, lsid, prefix):
        self.interfaceLock.acquire()
        self.interAreaPrefixes[prefix] = lsid
        self.interfaceLock.release()

    def del_intra_area_prefix_binding(self, prefix):
        self.interfaceLock.acquire()
        del self.interAreaPrefixes[prefix]
        self.interfaceLock.release()

    def get_intra_area_prefix_lsid(self, prefix):
        self.interfaceLock.acquire()
        lsid = self.interAreaPrefixes[prefix]
        self.interfaceLock.release()
        return lsid

    def check_intra_area_prefix_binding(self, lsid, prefix):
        self.interfaceLock.acquire()
        if prefix not in self.intraAreaPrefixes:
            self.intraAreaPrefixes[prefix] = lsid
        self.interfaceLock.release()

    def update_lsdb(self):
        lsdb = self.get_lsdb()
        lsdb.create_link_lsa(self)
        lsdb.create_router_lsa(self)

    def print_neighbors(self):
        for neighbor in self.neighborList.values():
            print neighbor.neighborId + '\t' + NEIGHBOR_STATE[neighbor.state]

    def get_br_state(self):
        flag = 0
        if self.isAbr:
            flag += 1
        return flag

    def add_adjacency(self, neighborId):
        lsdb = self.ospfDb.lsdbs[self.areaId]
        self.adjacencies.append(neighborId)
        if len(self.adjacencies) == 1:
            idx = lsdb.update_self_router_lsa_add_interface(self)
            for intf in lsdb.interfaceList:
                self.updateManager.send_multicast_update(intf, [idx])

    def remove_adjacency(self, neighborId):
        idx = None
        lsdb = self.ospfDb.lsdbs[self.areaId]
        try:
            self.adjacencies.remove(neighborId)
        except:
            addressManagment.print_service('Neighbor ' + str(neighborId) + ' already removed')
        if len(self.adjacencies) < 1:
            idx = lsdb.update_self_router_lsa_remove_interface(self)
            self.hasNeighbor = False
        return idx

    def set_interface_full_address(self, address):
        prefix, length = address.split('/')
        updates = []
        lsdb = self.get_lsdb()
        self.fullAddress = address
        idx = lsdb.update_self_link_lsa(self, self.address, address)
        updates.append(idx)
        if int(length) == 128:
            prefixOptions = int(0x02)  # LA bit for link address
            prefix = linkStateDatabase.Prefix(prefix, length, 0, prefixOptions)
            idx = lsdb.create_intra_area_prefix_lsa(self, linkStateDatabase.ROUTER_LSA, '0.0.0.0', [prefix],
                                                    self.routerId)
            if idx is None:
                idx = lsdb.update_self_intra_area_prefix_lsa_add(linkStateDatabase.ROUTER_LSA, self, [prefix])
            if idx is not None:
                updates.append(idx)
        else:
            if self.state == STATES['DR']:
                refLsid = '0.0.0.' + str(self.intfNumber)
                prefixOptions = int(0x00)
                prefixMetric = self.interfaceOutputCost
                trimmedAddress = addressManagment.trim_ip(prefix, length)
                prefix = linkStateDatabase.Prefix(trimmedAddress, length, prefixMetric, prefixOptions)
                idx = lsdb.create_intra_area_prefix_lsa(self, linkStateDatabase.NETWORK_LSA, refLsid, [prefix],
                                                        self.routerId)
                if idx is None:
                    idx = lsdb.update_self_intra_area_prefix_lsa_add(linkStateDatabase.NETWORK_LSA, self, [prefix])
                if idx is not None:
                    updates.append(idx)
        lsdb.update_area_interfaces(updates)

    def get_interface_network_lsa_idx(self):
        return linkStateDatabase.id(linkStateDatabase.NETWORK_LSA, self.routerId, '0.0.0.' + str(self.intfNumber))

    def is_dr(self):
        if self.designatedRouter == self.ospfDb.routerId:
            return True
        else:
            return False

    def create_network_lsa(self):
        lsdb = self.ospfDb.lsdbs[self.areaId]
        idx = lsdb.create_network_lsa(self)
        for intf in lsdb.interfaceList:
            self.updateManager.send_multicast_update(intf, [idx])

    def change_interface_cost(self, newCost):
        lsdb = self.ospfDb.lsdbs[self.areaId]
        self.interfaceOutputCost = newCost
        idx = lsdb.update_self_router_lsa_update_cost(self)
        for intf in lsdb.interfaceList:
            self.updateManager.send_multicast_update(intf, [idx])

    def shutdown(self):
        lsdb = self.ospfDb.lsdbs[self.areaId]
        self.active = False
        lsdb.remove_interface(self)
        lsas = []
        deadlsas = []
        if len(self.ospfDb.interfaceList) >= 1:
            if len(lsdb.interfaceList) >= 1:  # if there is still an interface on the area
                idx = lsdb.update_self_router_lsa_remove_interface(self)
                lsas.append(idx)
                lsdb.kill_self_link_lsa(self)
                if self.fullAddress is not None:
                    address, length = self.fullAddress.split('/')
                    if int(length) < 128:
                        refLSType = linkStateDatabase.NETWORK_LSA
                        address = addressManagment.trim_ip(address, length)
                    else:
                        refLSType = linkStateDatabase.ROUTER_LSA
                    idx, kill = lsdb.update_self_intra_area_prefix_lsa_remove(refLSType, self, [address])
                    if idx is not None:
                        if kill:
                            deadlsas.append(idx)
                        else:
                            lsas.append(idx)
                if self.is_dr():
                    idx = lsdb.kill_self_network_lsa(self)
                    deadlsas.append(idx)
                    idx = lsdb.kill_self_intra_area_prefix_lsa_network(self)
                    if idx is not None:
                        deadlsas.append(idx)
                for intf in lsdb.interfaceList:
                    if len(lsas) > 0:
                        intf.updateManager.send_multicast_update(intf, lsas)
                    if len(deadlsas) > 0:
                        intf.updateManager.send_multicast_kill_lsa(intf, deadlsas)
            else:  # no more interfaces on the same area
                lsdb.update_self_router_lsa_remove_interface(self)
                lsdb.kill_self_link_lsa(self)
                if self.fullAddress is not None:
                    lsdb.kill_self_intra_area_prefix_lsa_router(self)
                if self.is_dr():
                    lsdb.kill_self_network_lsa(self)
                    lsdb.kill_self_intra_area_prefix_lsa_network(self)
                    lsdb.routeManager.kill_route_manager(self.ospfDb)
                self.ospfDb.clear_lsdb(self.areaId)
                self.ospfDb.overlayLsdb.kill_overlay_routemanager()
            if len(self.ospfDb.lsdbs) <= 1:  # if router is no longer interArea
                self.ospfDb.update_is_inter_area(False)
        else:
            self.ospfDb.clear_ospfdb()


class Waiting:
    def __init__(self, interface, addrList):
        self.addr_info = (OSPF_GROUP_ADDRESS, 0, 0, interface.scopeId)
        self.receiver_socket = socket.socket(socket.AF_INET6, socket.SOCK_RAW, 89)  # OSPF flag number
        self.receiver_socket.bind(('', 10101))
        self.sender_socket = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_RAW)
        self.group_bin = socket.inet_pton(10, self.addr_info[0])  # 10 = AF_INET6
        self.mreq = self.group_bin + addressManagment.get_interface(interface.intfId)

        self.receiver_socket.setsockopt(socket.SOL_SOCKET, 25, interface.intfId + '\0')
        self.receiver_socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, self.mreq)
        self.receive_thread = threading.Thread(target=self.run_receive, args=(interface, addrList))
        self.receive_thread.daemon = True
        self.receive_thread.start()

        self.send_thread = threading.Thread(target=self.run_send, args=(interface,))
        self.send_thread.daemon = True
        self.send_thread.start()

    def run_receive(self, interface, addrList):
        global waiting
        while interface.active and waiting:
            data, sender = self.receiver_socket.recvfrom(5000)
            if sender[0].split('%')[0] not in addrList:
                check, destination = packetManager.check_data(data, sender, interface)
                if check and not check_sender(interface.address, sender):
                    packet = packetManager.unpack(data)
                    if int(packet.ospfHeader.type) == 1:
                        if packet.ospfHeader.routerId not in interface.neighborList:
                            addr = sender[0].split('%')[0]
                            newNeighbor = neighbors.Neighbor(packet.routerDeadInterval, packet.ospfHeader.routerId,
                                                             packet.routerPriority, addr,
                                                             packet.options, packet.designatedRouter,
                                                             packet.backupDesignatedRouter, packet.interfaceId,
                                                             interface.ospfDb, interface.areaId, interface)
                            interface.neighborList[packet.ospfHeader.routerId] = newNeighbor
                        else:
                            interface.neighborList[packet.ospfHeader.routerId].hello_received(interface, packet)

                        if packet.designatedRouter != '0.0.0.0':
                            interface.designatedRouter = packet.designatedRouter
                            interface.designatedRouterIntf = '0.0.0.' + str(packet.interfaceId)
                            if packet.backupDesignatedRouter != '0.0.0.0':
                                interface.backupDesignatedRouter = packet.backupDesignatedRouter
                                waiting = False
                else:
                    pass

    def run_send(self, interface):
        global waiting
        while waiting:
            interface.helloTimer -= 1
            if interface.helloTimer == 0:
                interface.helloTimer = interface.helloInterval
                SendHelloPacket(interface)
            time.sleep(1)


def check_sender(receiver, sender):
    sender = sender[0].split("%")[0]
    return sender == receiver


def elect_dr_bdr(interface, first):
    routerId = interface.ospfDb.routerId
    if interface.designatedRouter == '0.0.0.0' or interface.backupDesignatedRouter == '0.0.0.0':
        candidates = {}
        for neighbor in interface.neighborList.values():
            if neighbor.neighborPriority > 0:
                candidates[neighbor.neighborId] = neighbor
        """Elect DR"""
        if interface.designatedRouter == '0.0.0.0':
            dr = routerId
            drIntfId = interface.intfNumber
            prio = interface.routerPriority
            if not first:
                for candidate in candidates.values():
                    if candidate.neighborPriority > prio:
                        dr = candidate.neighborId
                        drIntfId = candidate.neighborIntfId
                        prio = candidate.neighborPriority
                    elif candidate.neighborPriority == prio:
                        if candidate.neighborId > dr:
                            dr = candidate.neighborId
                            drIntfId = candidate.neighborIntfId
                        else:
                            pass
                    else:
                        pass
            else:
                pass
            if dr == routerId:
                interface.state = STATES['DR']
            interface.designatedRouter = dr
            interface.designatedRouterIntf = '0.0.0.' + str(drIntfId)
        """Elect BDR"""
        if interface.designatedRouter == routerId:
            bdr = '0.0.0.0'
            prio = 0
        else:
            if interface.designatedRouter in candidates:
                try:
                    del candidates[interface.designatedRouter]
                except:
                    pass
            bdr = routerId
            prio = interface.routerPriority
        for candidate in candidates.values():
            if candidate.neighborPriority > prio:
                bdr = candidate.neighborId
                prio = candidate.neighborPriority
            elif candidate.neighborPriority == prio:
                if candidate.neighborId > bdr:
                    bdr = candidate.neighborId
                else:
                    pass
            else:
                pass
        interface.backupDesignatedRouter = bdr
        if bdr == routerId:
            interface.state = STATES['Backup']
        elif interface.designatedRouter != routerId and bdr != '0.0.0.0':
            interface.state = STATES['DR other']
        else:
            return
    else:
        if interface.designatedRouter == routerId:
            interface.state = STATES['DR']
        elif interface.backupDesignatedRouter == routerId:
            interface.state = STATES['Backup']
        else:
            interface.state = STATES['DR other']
        return


class Receiver:
    def __init__(self, interface):
        self.addr_info = (OSPF_GROUP_ADDRESS, 0, 0, interface.scopeId)
        self.receiver_socket = socket.socket(socket.AF_INET6, socket.SOCK_RAW, 89)  # OSPF flag number
        self.receiver_socket.bind(('', 10101))
        self.group_bin = socket.inet_pton(10, self.addr_info[0])  # 10 = AF_INET6
        self.mreq = self.group_bin + addressManagment.get_interface(interface.intfId)

        self.receiver_socket.setsockopt(socket.SOL_SOCKET, 25, interface.intfId + '\0')
        self.receiver_socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, self.mreq)
        self.receiverThread = threading.Thread(target=self.receiver, args=(interface,))
        self.receiverThread.daemon = True
        self.receiverThread.start()

    def receiver(self, intf):
        while intf.active:
            packet, sender = self.receiver_socket.recvfrom(5000)
            workerThread = threading.Thread(target=self.run, args=(intf, packet, sender))
            workerThread.daemon = True
            workerThread.start()

    def run(self, intf, unpackedPacket, sender):
        if sender[0].split('%')[0] not in intf.ospfDb.addressList:
            check, destination = packetManager.check_data(unpackedPacket, sender, intf)
            if check and not check_sender(intf.address, sender):
                packet = packetManager.unpack(unpackedPacket)
                packet.process(sender, intf, intf.get_lsdb())


class SendHelloPacket:
    def __init__(self, interface):
        self.addr_info = (OSPF_GROUP_ADDRESS, 0, 0, interface.scopeId)
        self.sender_socket = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_RAW)
        self.thread = threading.Thread(target=self.run, args=(interface,))
        self.thread.daemon = True
        self.thread.start()

    def run(self, interface):
        outpacket = ospf_packet_build(1, interface, OSPF_GROUP_ADDRESS, None)
        self.sender_socket.sendto(outpacket, self.addr_info)


class UpdateManager:
    def __init__(self, intf):
        self.sender_socket = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_RAW)
        self.updateList = {}  # {threadnum:set(up1, up2)}
        self.threadnum = 0
        self.sentUpdates = []
        self.intf = intf

    def get_thread_number(self):
        out = self.threadnum
        self.threadnum += 1
        return out

    def send_unicast_update(self, address, interface, requests):  # requests = [idx1, idx2, ...]
        if interface.active and interface.hasNeighbor:
            threadnum = self.get_thread_number()
            self.updateList[threadnum] = set()
            unicast_thread = threading.Thread(target=self.send_unicast_update_worker,
                                              args=(threadnum, address, interface, requests))
            unicast_thread.daemon = True
            unicast_thread.start()

    def send_unicast_update_worker(self, threadnum, address, interface, requests):
        lsdb = interface.ospfDb.lsdbs[interface.areaId]
        overlayLsdb = interface.ospfDb.overlayLsdb
        addrInfo = (address, 0, 0, interface.scopeId)
        if interface.hasNeighbor:
            self.append_updates(threadnum, requests)
        while interface.active and interface.hasNeighbor and len(self.updateList[threadnum]) > 0:
            requests = self.request_organizer(self.updateList[threadnum].copy(), lsdb, overlayLsdb)
            packet = ospf_packet_build(4, interface, addrInfo[0], requests)
            self.sender_socket.sendto(packet, addrInfo)
            time.sleep(10)
        del (self.updateList[threadnum])

    def send_unicast_kill_update(self, address, interface, request):
        if interface.active and interface.hasNeighbor:
            threadnum = self.get_thread_number()
            self.updateList[threadnum] = set()
            unicast_thread = threading.Thread(target=self.send_unicast_kill_update_worker,
                                              args=(threadnum, address, interface, request))
            unicast_thread.daemon = True
            unicast_thread.start()

    def send_unicast_kill_update_worker(self, threadnum, address, interface, request):
        lsdb = interface.ospfDb.lsdbs[interface.areaId]
        overlayLsdb = interface.ospfDb.overlayLsdb
        addrInfo = (address, 0, 0, interface.scopeId)
        if interface.hasNeighbor:
            self.append_updates(threadnum, request)
        while interface.active and interface.hasNeighbor and len(self.updateList[threadnum]) > 0:
            requests = self.dead_request_organizer(request, lsdb, overlayLsdb)
            packet = ospf_packet_build(4, interface, addrInfo[0], requests)
            self.sender_socket.sendto(packet, addrInfo)
            time.sleep(10)
        del (self.updateList[threadnum])

    def send_multicast_update(self, interface, requests):  # requests = [idx1, idx2, ...]
        if interface.active and interface.hasNeighbor:
            threadnum = self.get_thread_number()
            self.updateList[threadnum] = set()
            multicast_thread = threading.Thread(target=self.send_multicast_update_worker,
                                                args=(threadnum, interface, requests))
            multicast_thread.daemon = True
            multicast_thread.start()

    def send_multicast_update_worker(self, threadnum, interface, requests):
        lsdb = interface.ospfDb.lsdbs[interface.areaId]
        overlayLsdb = interface.ospfDb.overlayLsdb
        addrInfo = (OSPF_GROUP_ADDRESS, 0, 0, interface.scopeId)
        self.append_updates(threadnum, requests)
        while interface.active and interface.hasNeighbor and len(self.updateList[threadnum]) > 0:
            requests = self.request_organizer(self.updateList[threadnum].copy(), lsdb, overlayLsdb)
            packet = ospf_packet_build(4, interface, addrInfo[0], requests)
            self.sender_socket.sendto(packet, addrInfo)
            time.sleep(10)
        del (self.updateList[threadnum])

    def request_organizer(self, requests, lsdb, overlayLsdb):
        lsaTypes = {int(0x2001): lsdb.routerLS,
                    int(0x2002): lsdb.networkLS,
                    int(0x2003): lsdb.interAreaPrefixLS,
                    int(0x0008): lsdb.linkLS,
                    int(0x2009): lsdb.intraAreaLS,
                    int(0x400a): overlayLsdb.abrLS,
                    int(0x400b): overlayLsdb.prefixLS,
                    int(0x400c): overlayLsdb.asbrLS}
        out = {}  # {idx: LSA}
        for request in requests:
            if request.lsType == 8:
                lsa = self.intf.get_lsdb().linkLS[request.id]
                if lsa.interface != self.intf.intfId:
                    continue
            try:
                out[request.id] = lsaTypes[request.lsType][request.id]
            except:
                try:
                    out[request.id] = lsdb.deadLSAs[request.id]
                except:
                    try:
                        out[request.id] = overlayLsdb.deadLSAs[request.id]
                    except:
                        addressManagment.print_service('Failed to send update ' + str(request.id))
                        addressManagment.print_service('Router_linux: ')
        return out

    def send_multicast_kill_lsa(self, interface, idxLsa):
        if interface.active and interface.hasNeighbor:
            threadnum = self.get_thread_number()
            self.updateList[threadnum] = set()
            thread = threading.Thread(target=self.send_multicast_kill_lsa_worker, args=(threadnum, interface, idxLsa))
            thread.daemon = True
            thread.start()

    def send_multicast_kill_lsa_worker(self, threadnum, interface, idxList):
        lsdb = interface.ospfDb.lsdbs[interface.areaId]
        overlayLsdb = interface.ospfDb.overlayLsdb
        if interface.hasNeighbor:
            addrInfo = (OSPF_GROUP_ADDRESS, 0, 0, interface.scopeId)
            self.append_updates(threadnum, idxList)
            while interface.hasNeighbor and interface.active and len(self.updateList[threadnum]) > 0:
                idxLsa = self.dead_request_organizer(idxList, lsdb, overlayLsdb)
                packet = ospf_packet_build(4, interface, OSPF_GROUP_ADDRESS, idxLsa)
                self.sender_socket.sendto(packet, addrInfo)
                time.sleep(10)
            del (self.updateList[threadnum])

    def dead_request_organizer(self, requests, lsdb, overlayLsdb):
        lsaTypes = {int(0x2001): lsdb,
                    int(0x2002): lsdb,
                    int(0x2003): lsdb,
                    int(0x0008): lsdb,
                    int(0x2009): lsdb,
                    int(0x400a): overlayLsdb,
                    int(0x400b): overlayLsdb,
                    int(0x400c): overlayLsdb}
        out = {}  # {idx: LSA}
        for requestId in requests:
            lsType = int(requestId.split('-')[0])
            while True:
                try:
                    out[requestId] = lsaTypes[lsType].deadLSAs[requestId]
                    break
                except:
                    threading._sleep(1)
        return out

    def append_updates(self, threadnum, list):
        for req in list:
            try:
                req.id
            except:
                aux = req.split('-')
                req = packetManager.Request(0, int(aux[0]), aux[2], aux[1])
            if req.lsType == 8:
                try:
                    lsa = self.intf.get_lsdb().linkLS[req.id]
                except:
                    print 'failed to find ' + str(req.id)
                    continue
                if lsa.interface != self.intf.intfId:
                    continue
            self.updateList[threadnum].add(req)

    def acknowledge_received(self, idx, lsdb, seqnum):
        if len(self.updateList) > 0:
            for thread in self.updateList.keys():
                rem = set()
                for update in self.updateList[thread]:
                    if idx == update.id:
                        try:
                            if lsdb.dbs[update.lsType][update.id].sequenceNumber == seqnum:
                                rem.add(update)
                            elif update.lsType == 8:
                                rem.add(update)
                            break
                        except:  # if update in kill lsas
                            try:
                                if lsdb.deadLSAs[update.id].sequenceNumber == seqnum:
                                    rem.add(update)
                                break
                            except:
                                rem.add(update)
                            break
                try:
                    self.updateList[thread] = self.updateList[thread].difference(rem)
                except:
                    addressManagment.print_service('Failed to ack ' + str(rem))
                    addressManagment.print_service('Current updates ' + str(self.updateList[thread]) + '\nRouter_linux: ')

    def acknowledge_update(self, interface, lsaHeaders, destAddress):
        thread = threading.Thread(target=self.send_acknowledge, args=(interface, lsaHeaders, destAddress))
        thread.daemon = True
        thread.start()

    def send_acknowledge(self, interface, lsaHeaders, destAddress):
        addrInfo = (destAddress, 0, 0, interface.scopeId)
        packet = ospf_packet_build(5, interface, destAddress, lsaHeaders)
        self.sender_socket.sendto(packet, addrInfo)


def ospf_packet_build(type, interface, sendTo, body):  # body is a list[] with the variable parameters
    ospfHeader = packetManager.OSPFHeader()
    ospfHeader.init(type, interface.ospfDb.get_router_id, interface.areaId)
    if type == 1:
        packet = packetManager.HelloPacket(ospfHeader)
        packetbody = packet.packet_body(interface)
    elif type == 2:
        packet = packetManager.DBDescription(ospfHeader)
        ddseq = body[0]
        ack = body[1]
        neighbor = body[2]
        ims = body[3]
        packetbody = packet.packet_body(ddseq, ack, interface, neighbor, ims)
    elif type == 3:
        packet = packetManager.LSRequest(ospfHeader)
        neighbor = body[0]
        lsdb = body[1]
        packetbody = packet.packet_body(neighbor, lsdb)
    elif type == 4:
        packet = packetManager.LSUpdate(ospfHeader)
        packetbody = packet.packet_body(interface, body)
    elif type == 5:
        packet = packetManager.LSAcknowledge(ospfHeader)
        packetbody = packet.packet_body(body)
    else:
        print '\nUnknown type\n'
        return None
    packedoutput = packet.build(interface, sendTo, packetbody, type)
    return packedoutput
