import socket
import struct
import sys
import neighbors
import interface
import linkStateDatabase
import addressManagment

ospf_group_address = 'ff02::5'

""" Headers """


def ipv6_header(pckt_length, source, sendTo):
    IPversion_traficClass_flowLabel = struct.pack('!I', int(0b01101110000000000000000000000000))
    payload_length = struct.pack('!H', pckt_length)
    next_header = struct.pack('!B', 89)  # OSPF
    hop_limit = struct.pack('!B', 1)
    sourceAddr = socket.inet_pton(socket.AF_INET6, source)
    destAddr = socket.inet_pton(socket.AF_INET6, sendTo)
    return [IPversion_traficClass_flowLabel, payload_length, next_header, hop_limit, sourceAddr, destAddr]


def ipv6_pseudoheader(source, sendTo, pckt_length):
    sourceAddr = socket.inet_pton(socket.AF_INET6, source)
    destAddr = socket.inet_pton(socket.AF_INET6, sendTo)
    return [sourceAddr, destAddr, struct.pack('!H', 0) + struct.pack('!H', pckt_length),
            struct.pack('!H', 0) + struct.pack('!H', 89)]


def ospf_header(type, pckt_length, routerID, intf):
    version = struct.pack('!B', 3)  # ospfv3
    type = struct.pack('!B', type)  # Hello packet = 1
    packt_length = struct.pack('!H', pckt_length)
    rid = socket.inet_aton(routerID)
    aid = socket.inet_aton(intf.areaId)
    chksum = struct.pack('!H', 0)
    instance = struct.pack('!B', 0)  # 0 for single instance ospf
    fillerHeader = struct.pack('!B', 0)
    return [version, type, packt_length, rid, aid, chksum, instance, fillerHeader]


def lsa_header(lsTableEntry, linkStateID, lsType, local, u, s1):  # local=0 -> yes, local=1, no
    lsAge = struct.pack('!H', lsTableEntry.age)
    lsType = struct.pack('!H', ls_type(u, s1, local, lsType))
    linkStateID = socket.inet_aton(linkStateID)
    advertisingRouter = socket.inet_aton(lsTableEntry.advRouter)
    sequenceNumber = struct.pack('!I', lsTableEntry.sequenceNumber)
    return [lsAge, lsType, linkStateID, advertisingRouter, sequenceNumber]


""" Clases """


class OSPFHeader():
    def __init__(self):
        self.version = None
        self.type = None
        self.packetLength = None
        self.routerId = None
        self.areaId = None
        self.checksum = None
        self.instanceId = None

    def init(self, type, routerID, areaID):
        self.version = 3
        self.type = type
        self.routerId = routerID
        self.areaId = areaID
        self.instanceId = 0

    def unpack(self, packedheader):
        headerFormat = '!BBHIIHBB'
        header = struct.unpack(headerFormat, packedheader)
        self.version = int(header[0])
        self.type = int(header[1])
        self.packetLength = str(header[2])
        self.routerId = ip_converter(header[3])
        self.areaId = ip_converter(header[4])
        self.checksum = str(header[5])
        self.instanceId = str(header[6])


class Packet():
    def __init__(self, ospfHeader):
        self.lsas = {}
        self.ospfHeader = ospfHeader

    def unpack_lsa_header(self, header):
        format = '!HHIIIHH'
        header = struct.unpack(format, header)
        lsage = int(header[0])
        lstype = int(header[1])
        linkStateID = ip_converter(header[2])
        advertisingRouter = ip_converter(header[3])
        sequenceNumber = int(header[4])
        checksum = struct.pack('!H', header[5])
        length = int(header[6])
        lsaHeader = LSAHeader(lsage, lstype, linkStateID, advertisingRouter, sequenceNumber)
        lsaHeader.checksum = checksum
        lsaHeader.length = length
        lsa = None
        if lsaHeader.lsType == int(0x2001) or lsaHeader.lsType == 1:
            lsa = LSAType1(lsaHeader)
        elif lsaHeader.lsType == int(0x2002) or lsaHeader.lsType == 2:
            lsa = LSAType2(lsaHeader)
        elif lsaHeader.lsType == int(0x2003) or lsaHeader.lsType == 3:
            lsa = LSAType3(lsaHeader)
        elif lsaHeader.lsType == int(0x2008) or lsaHeader.lsType == 8:
            lsa = LSAType8(lsaHeader)
        elif lsaHeader.lsType == int(0x2009) or lsaHeader.lsType == 9:
            lsa = LSAType9(lsaHeader)
        elif lsaHeader.lsType == int(0x400a):
            lsa = LSAType10(lsaHeader)
        elif lsaHeader.lsType == int(0x400b):
            lsa = LSAType11(lsaHeader)
        elif lsaHeader.lsType == int(0x400c):
            lsa = LSAType12(lsaHeader)
        self.lsas[lsaHeader.idx] = lsa
        return lsa

    def unpack(self, packet):
        pass

    def process(self, sender, intf, lsdb):
        pass

    def build(self, intf, sendTo, packetBody, packetType):
        packetLength = load_size(packetBody) + 16
        OSPFheader = ospf_header(packetType, packetLength, intf.routerId, intf)
        IPv6header = ipv6_header(packetLength, intf.address, sendTo)
        IPv6PseudoHeader = ipv6_pseudoheader(intf.address, sendTo, packetLength)
        checksum = checksum_ospf((IPv6PseudoHeader, OSPFheader, packetBody))
        OSPFheader[5] = checksum
        packet = [IPv6header, OSPFheader] + packetBody
        packet = packet_builder(packet)
        return packet


class LSAHeader():
    def __init__(self, lsage, lstype, linkstateid, advertisingrouter, seqnum):
        self.lsAge = lsage
        self.lsType = int(lstype)
        self.linkStateId = linkstateid
        self.advertisingRouter = advertisingrouter
        self.sequenceNumber = seqnum
        self.idx = link_state_id(lstype, advertisingrouter, linkstateid)


class LSAPacket():
    def __init__(self, lsaHeader):
        self.lsaHeader = lsaHeader

    def unpack(self, packedLsa):
        pass

    def build(self, lsEntry, full):
        pass

    def process(self, intf, dead):
        addressManagment.print_service('proccessing type ' + str((self.lsaHeader.lsType)) + ' idx: ' + str(self.lsaHeader.idx))


class LSAType1(LSAPacket):
    def __init__(self, lsaHeader):
        LSAPacket.__init__(self, lsaHeader)

    def unpack(self, packedLsa):
        format = '!BBH'
        aux = struct.unpack(format, packedLsa[:4])
        self.flags = int(aux[0])
        self.options = int(aux[2])
        self.entries = {}
        len = self.lsaHeader.length
        if len > 24:
            entries = (len - 24) / 16
            format = '!BBHIII'
            for i in range(0, entries):
                start = 4 + i * 16
                finish = start + 16
                aux = struct.unpack(format, packedLsa[start:finish])
                aux = RouterDescription(int(aux[0]), int(aux[1]), int(aux[2]), aux[3], aux[4], ip_converter(aux[5]), i)
                self.entries[aux.interfaceId] = aux

    def build(self, routerLSEntry, full):
        linkStateID = self.lsaHeader.linkStateId
        flags = routerLSEntry.get_flags()
        flags = struct.pack('!B', buildWVEB(flags[0], flags[1], flags[2], flags[3]))
        options = struct.pack('!BH', 0, build_lsa_options(1, 1, 1, 1))
        LSApacket = [flags, options]
        if len(routerLSEntry.linkCount) > 0:
            order = {}
            for entry in routerLSEntry.linkCount.values():
                aux = struct.pack('!BBHII', entry.type, 0, entry.metric, entry.interfaceId,
                                  int(entry.neighborInterfaceId))
                aux += socket.inet_aton(entry.neighborRouterId)
                order[entry.order] = aux
            for value in sorted(order.keys()):
                LSApacket.append(order[value])
        pcktLen = 20
        for info in LSApacket:
            pcktLen += len(info)
        length = struct.pack('!H', pcktLen)
        """LSA header"""
        LSAheader = lsa_header(routerLSEntry, linkStateID, 1, 1, 0, 0)
        checksum = fletcher_checksum((LSAheader, (struct.pack('!H', 0), length), LSApacket))
        LSAheader.append(checksum)
        LSAheader.append(length)
        if full:
            return LSAheader + LSApacket, pcktLen
        else:
            return LSAheader, pcktLen

    def process(self, intf, dead):
        lsdb = intf.get_lsdb()
        advRtr = self.lsaHeader.advertisingRouter
        lsAge = self.lsaHeader.lsAge
        seq = self.lsaHeader.sequenceNumber
        brState = self.get_br_state()
        links = self.entries
        options = self.options
        lsid = self.lsaHeader.linkStateId
        lsdb.update_router_lsa(advRtr, lsAge, seq, brState, links, options, lsid, dead)

    def get_br_state(self):
        if int(self.flags) >= 1:
            return True
        else:
            return False


class RouterDescription():
    def __init__(self, type, reserved, metric, interfaceId, neighborIntfId, neighborRtrId, order):
        self.type = type
        self.reserved = reserved
        self.metric = metric
        self.interfaceId = interfaceId
        self.neighborInterfaceId = neighborIntfId
        self.neighborRouterId = neighborRtrId
        self.order = int(order)

    def print_link(self):
        print('\n\t\tLink Type: ' + str(self.type))
        print('\t\tMetric: ' + str(self.metric))
        print('\t\tInterface ID: ' + str(self.interfaceId))
        print('\t\tNeighbor Interface ID: ' + str(self.neighborInterfaceId))
        print('\t\tNeighbor Router ID: ' + self.neighborRouterId)


class LSAType2(LSAPacket):
    def __init__(self, lsaHeader):
        LSAPacket.__init__(self, lsaHeader)

    def unpack(self, packedLsa):
        format = '!BBH'
        aux = struct.unpack(format, packedLsa[:4])
        self.reserved = int(aux[0])
        self.options = int(aux[2])
        nbrRouter = (self.lsaHeader.length - 24) / 4
        self.attachedRouters = []
        format = '!' + nbrRouter * 'I'
        aux = struct.unpack(format, packedLsa[4:4 + nbrRouter * 4])
        for router in aux:
            self.attachedRouters.append(ip_converter(router))

    def build(self, networkLSA, full):
        linkStateID = self.lsaHeader.linkStateId
        options = struct.pack('!HH', 0, build_lsa_options(1, 1, 1, 1))
        LSApacket = [options]
        for router in networkLSA.routerList:
            LSApacket.append(socket.inet_aton(router))
        pcktLen = 20
        for info in LSApacket:
            pcktLen += len(info)
        lenght = struct.pack('!H', pcktLen)
        """LSA header"""
        LSAheader = lsa_header(networkLSA, linkStateID, 2, 1, 0, 0)
        checksum = fletcher_checksum((LSAheader, (struct.pack('!H', 0), lenght), LSApacket))
        LSAheader.append(checksum)
        LSAheader.append(lenght)
        if full:
            return (LSAheader + LSApacket, pcktLen)
        else:
            return (LSAheader, pcktLen)

    def process(self, intf, dead):
        lsdb = intf.get_lsdb()
        advRtr = self.lsaHeader.advertisingRouter
        lsid = self.lsaHeader.linkStateId
        age = self.lsaHeader.lsAge
        seq = self.lsaHeader.sequenceNumber
        options = self.options
        list = self.attachedRouters
        lsdb.update_network_lsa(advRtr, lsid, age, seq, options, list, dead)


class LSAType3(LSAPacket):
    def __init__(self, lsaHeader):
        LSAPacket.__init__(self, lsaHeader)

    def unpack(self, packedLsa):
        start = 0
        start += 1  # padding
        metric = struct.unpack('!BH', packedLsa[start:start + 3])[1]
        start += 3
        length = struct.unpack('!B', packedLsa[start:start + 1])[0]
        start += 1
        options = struct.unpack('!B', packedLsa[start:start + 1])[0]
        start += 1
        start += 2  # padding
        length /= 8
        prefix = packedLsa[start:start + length]
        prefix += '\x00' * (16 - length)
        prefix = socket.inet_ntop(socket.AF_INET6, prefix)
        self.prefix = linkStateDatabase.Prefix(prefix, length * 8, metric, options)

    def build(self, interPrefixLSA, full):
        linkStateID = self.lsaHeader.linkStateId
        padding = struct.pack('!B', 0)
        LSApacket = []
        prefix = interPrefixLSA.prefix
        metric = struct.pack('!BH', 0, prefix.metric)
        length = prefix.length
        prefixLength = struct.pack('!B', int(length))
        prefixOptions = struct.pack('!B', prefix.options)
        address = socket.inet_pton(socket.AF_INET6, prefix.address)
        address = address[:length / 8]
        LSApacket += [padding, metric, prefixLength, prefixOptions, 2 * padding, address]
        pcktLen = 20
        for info in LSApacket:
            pcktLen += len(info)
        length = struct.pack('!H', pcktLen)
        """LSA header"""
        LSAheader = lsa_header(interPrefixLSA, linkStateID, 3, 1, 0, 0)
        checksum = fletcher_checksum((LSAheader, (struct.pack('!H', 0), length), LSApacket))
        LSAheader.append(checksum)
        LSAheader.append(length)
        if full:
            return (LSAheader + LSApacket, pcktLen)
        else:
            return (LSAheader, pcktLen)

    def process(self, intf, dead):
        lsdb = intf.get_lsdb()
        advRtr = self.lsaHeader.advertisingRouter
        age = self.lsaHeader.lsAge
        seq = self.lsaHeader.sequenceNumber
        metric = self.prefix.metric
        prefixLength = self.prefix.length
        prefixOptions = self.prefix.options
        prefix = self.prefix.address
        lsid = self.lsaHeader.linkStateId
        lsdb.update_inter_area_prefix_lsa(advRtr, age, seq, metric, prefixLength, prefixOptions, prefix, lsid, dead)


class LSAType8(LSAPacket):
    def __init__(self, lsaHeader):
        LSAPacket.__init__(self, lsaHeader)

    def unpack(self, packedLsa):
        format = '!BBH'
        aux = struct.unpack(format, packedLsa[:4])
        self.routerPriority = int(aux[0])
        self.options = int(aux[2])
        self.linkLocalIntAddress = socket.inet_ntop(socket.AF_INET6, packedLsa[4:20])
        prefixNbr = int(struct.unpack('!I', packedLsa[20:24])[0])
        self.numPrefixes = prefixNbr
        if self.numPrefixes != 0:
            prefixList = {}
            for i in range(0, prefixNbr):
                metric = int(struct.unpack('!H', packedLsa[26 + i * 20:28 + i * 20])[0])
                length = int(struct.unpack('!B', packedLsa[24 + i * 20:25 + i * 20])[0])
                options = int(struct.unpack('!B', packedLsa[25 + i * 20:26 + i * 20])[0])
                finish = length / 8
                start = 28 + 20 * i
                address = packedLsa[start:start + finish]
                while len(address) < 16:
                    address += '\x00'
                address = socket.inet_ntop(socket.AF_INET6, address)
                prefix = linkStateDatabase.Prefix(address, length, metric, options)
                prefixList[prefix.address] = prefix
            self.prefixList = prefixList

    def build(self, linkLS, full):
        linkStateID = self.lsaHeader.linkStateId
        routerPriority = struct.pack('!B', linkLS.priority)
        options = struct.pack('!BH', 0, build_lsa_options(1, 1, 1, 1))
        linkLocalAddress = socket.inet_pton(socket.AF_INET6, linkLS.address)
        prefixNbr = struct.pack('!I', linkLS.numberPrefixes)
        LSApacket = [routerPriority, options, linkLocalAddress, prefixNbr]
        for prefix in linkLS.prefixList.values():
            LSApacket.append(struct.pack('!B', int(prefix.length)))
            LSApacket.append(struct.pack('!B', int(prefix.options)))
            LSApacket.append(struct.pack('!H', 0))
            LSApacket.append(socket.inet_pton(socket.AF_INET6, prefix.address))
        pcktLen = 20
        for info in LSApacket:
            pcktLen += len(info)
        length = struct.pack('!H', pcktLen)
        """LSA header"""
        LSAheader = lsa_header(linkLS, linkStateID, 8, 0, 0, 0)
        checksum = fletcher_checksum((LSAheader, (struct.pack('!H', 0), length), LSApacket))
        LSAheader.append(checksum)
        LSAheader.append(length)
        if full:
            return LSAheader + LSApacket, pcktLen
        else:
            return LSAheader, pcktLen

    def process(self, intf, dead):
        LSAPacket.process(self, intf, dead)
        lsdb = intf.get_lsdb()
        advRtr = self.lsaHeader.advertisingRouter
        age = self.lsaHeader.lsAge
        seq = self.lsaHeader.sequenceNumber
        lsid = self.lsaHeader.linkStateId
        priority = self.routerPriority
        options = self.options
        addr = self.linkLocalIntAddress
        try:
            list = self.prefixList
        except:
            list = None
        lsdb.update_link_lsa(advRtr, age, seq, lsid, priority, options, addr, list, intf.intfId, dead)


class LSAType9(LSAPacket):
    def __init__(self, lsaHeader):
        LSAPacket.__init__(self, lsaHeader)

    def unpack(self, packedLsa):
        prefixNbr = int(struct.unpack('!H', packedLsa[:2])[0])
        format = '!HII'
        aux = struct.unpack(format, packedLsa[2:12])
        self.numPrefixes = prefixNbr
        self.refLsType = aux[0]
        self.refLsId = ip_converter(aux[1])
        self.refAdvRouter = ip_converter(aux[2])
        prefixList = {}
        for i in range(0, prefixNbr):
            metric = int(struct.unpack('!H', packedLsa[14 + i * 20:16 + i * 20])[0])
            length = int(struct.unpack('!B', packedLsa[12 + i * 20:13 + i * 20])[0])
            options = int(struct.unpack('!B', packedLsa[13 + i * 20:14 + i * 20])[0])
            finish = length / 8
            start = 16 + 20 * i
            address = packedLsa[start:start + finish]
            while len(address) < 16:
                address += '\x00'
            address = socket.inet_ntop(socket.AF_INET6, address)
            prefix = linkStateDatabase.Prefix(address, length, metric, options)
            prefixList[prefix.address] = prefix
        self.prefixList = prefixList

    def build(self, intraLS, full):
        linkStateID = self.lsaHeader.linkStateId
        nbrPrefixes = struct.pack('!H', len(intraLS.prefixList))
        refLStype = struct.pack('!H', intraLS.refLsType)
        refLSID = socket.inet_aton(intraLS.refLsId)
        refAdvRouter = socket.inet_aton(intraLS.refAdvRouter)
        LSApacket = [nbrPrefixes, refLStype, refLSID, refAdvRouter]
        for prefix in intraLS.prefixList.values():
            length = int(prefix.length)
            LSApacket.append(struct.pack('!B', length))
            if prefix.options is None:
                LSApacket.append(struct.pack('!B', 0))
            else:
                LSApacket.append(struct.pack('!B', prefix.options))
            LSApacket.append(struct.pack('!H', prefix.metric))
            addr = socket.inet_pton(socket.AF_INET6, prefix.address)
            LSApacket.append(addr[:length / 8])
        pcktLen = 20
        for info in LSApacket:
            pcktLen += len(info)
        length = struct.pack('!H', pcktLen)
        """LSA header"""
        LSAheader = lsa_header(intraLS, linkStateID, 9, 1, 0, 0)
        checksum = fletcher_checksum((LSAheader, (struct.pack('!H', 0), length), LSApacket))
        LSAheader.append(checksum)
        LSAheader.append(length)
        if full:
            return LSAheader + LSApacket, pcktLen
        else:
            return LSAheader, pcktLen

    def process(self, intf, dead):
        lsdb = intf.get_lsdb()
        advRtr = self.lsaHeader.advertisingRouter
        lsid = self.lsaHeader.linkStateId
        seq = self.lsaHeader.sequenceNumber
        age = self.lsaHeader.lsAge
        refLSType = self.refLsType
        refLSId = self.refLsId
        list = self.prefixList
        refAdvRtr = self.refAdvRouter
        lsdb.update_intra_area_prefix_lsa(advRtr, lsid, seq, age, refLSType, refLSId, list, refAdvRtr, dead)


class LSAType10(LSAPacket):
    def __init__(self, lsaHeader):
        LSAPacket.__init__(self, lsaHeader)

    def unpack(self, packedLsa):
        prefixNbr = int(len(packedLsa) / 5)
        neighborList = {}
        start = 0
        for i in range(prefixNbr):
            metric = struct.unpack('!B', packedLsa[start:start + 1])[0]
            start += 1
            rid = socket.inet_ntoa(packedLsa[start:start + 4])
            start += 4
            check = rid.split('.')
            if not (check[0] == check[1] and check[0] == check[2] and check[0] == check[3]):
                continue
            neighborList[rid] = metric
        self.neighbors = neighborList

    def build(self, unknownLS, full):
        linkStateID = self.lsaHeader.linkStateId
        packet = []
        for neighbor in unknownLS.get_neighbor_list().keys():
            packet.append(struct.pack('!B', unknownLS.neighborList[neighbor]))
            packet.append(socket.inet_aton(neighbor))
        pcktLen = len(unknownLS.neighborList) * (1 + 4) + 20
        length = struct.pack('!H', pcktLen)
        LSAheader = lsa_header(unknownLS, linkStateID, 10, 0, 1, 1)
        checksum = fletcher_checksum((LSAheader, (struct.pack('!H', 0), length), packet))
        LSAheader.append(checksum)
        LSAheader.append(length)
        if full:
            return (LSAheader + packet, pcktLen)
        else:
            return (LSAheader, pcktLen)

    def process(self, intf, dead):
        overlay = intf.ospfDb.overlayLsdb
        advRtr = self.lsaHeader.advertisingRouter
        lsid = self.lsaHeader.linkStateId
        seq = self.lsaHeader.sequenceNumber
        age = self.lsaHeader.lsAge
        neighborList = self.neighbors
        overlay.update_abrls(advRtr, age, seq, lsid, neighborList, dead)


class LSAType11(LSAPacket):
    def __init__(self, lsaHeader):
        LSAPacket.__init__(self, lsaHeader)

    def unpack(self, packedLsa):
        start = 0
        max = len(packedLsa)
        prefixList = {}
        while start < max:
            metric = int(struct.unpack('!B', packedLsa[start:start + 1])[0])
            start += 1
            length = int(struct.unpack('!B', packedLsa[start:start + 1])[0])
            start += 1
            options = int(struct.unpack('!B', packedLsa[start:start + 1])[0])
            start += 1
            length /= 8
            prefix = packedLsa[start:start + length]
            prefix += '\x00' * (16 - length)
            address = socket.inet_ntop(socket.AF_INET6, prefix)
            start += length
            prefix = linkStateDatabase.Prefix(address, length*8, metric, options)
            prefixList[address] = prefix
        self.prefixes = prefixList

    def build(self, unknownLS, full):
        linkStateID = self.lsaHeader.linkStateId
        packet = []
        for prefix in unknownLS.prefixes.values():
            length = int(prefix.length)
            packet.append(struct.pack('!B', prefix.metric))
            packet.append(struct.pack('!B', length))
            if prefix.options == 'none':
                options = 0
            else:
                options = int(prefix.options)
            packet.append(struct.pack('!B', options))
            addr = socket.inet_pton(socket.AF_INET6, prefix.address)
            packet.append(addr[:length / 8])
        pcktLen = 0
        for section in packet:
            pcktLen += len(section)
        pcktLen += 20
        length = struct.pack('!H', pcktLen)
        LSAheader = lsa_header(unknownLS, linkStateID, 11, 0, 1, 1)
        checksum = fletcher_checksum((LSAheader, (struct.pack('!H', 0), length), packet))
        LSAheader.append(checksum)
        LSAheader.append(length)
        if full:
            return LSAheader + packet, pcktLen
        else:
            return LSAheader, pcktLen

    def process(self, intf, dead):
        overlay = intf.ospfDb.overlayLsdb
        advRtr = self.lsaHeader.advertisingRouter
        lsid = self.lsaHeader.linkStateId
        seq = self.lsaHeader.sequenceNumber
        age = self.lsaHeader.lsAge
        prefixes = self.prefixes
        overlay.update_prefixls(advRtr, age, seq, lsid, prefixes, dead)


class LSAType12(LSAPacket):
    def __init__(self, lsaHeader):
        LSAPacket.__init__(self, lsaHeader)

    def unpack(self, packedLsa):
        prefixNbr = int(len(packedLsa) / 5)
        asbrs = {}
        start = 0
        for i in range(prefixNbr):
            metric = packedLsa[start:start + 1]
            start += 1
            asbr = socket.inet_ntoa(packedLsa[start:start + 4])
            start += 4
            asbrs[asbr] = metric
        self.asbrs = asbrs

    def build(self, unknownLS, full):
        linkStateID = self.lsaHeader.linkStateId
        packet = []
        for asbr in unknownLS.asbrs:
            packet.append(struct.pack('!B', unknownLS.asbrs[asbr]))
            packet.append(socket.inet_aton(asbr))
        pcktLen = len(unknownLS.asbrs) * (1 + 4) + 20
        length = struct.pack('!H', pcktLen)
        LSAheader = lsa_header(unknownLS, linkStateID, 12, 0, 1, 1)
        checksum = fletcher_checksum((LSAheader, (struct.pack('!H', 0), length), packet))
        LSAheader.append(checksum)
        LSAheader.append(length)
        if full:
            return (LSAheader + packet, pcktLen)
        else:
            return (LSAheader, pcktLen)

    def process(self, intf, dead):
        overlay = intf.ospfDb.overlayLsdb
        advRtr = self.lsaHeader.advertisingRouter
        lsid = self.lsaHeader.linkStateId
        seq = self.lsaHeader.sequenceNumber
        age = self.lsaHeader.lsAge
        neighbors = self.asbrs
        overlay.update_asbrls(advRtr, age, seq, lsid, neighbors, dead)


class HelloPacket(Packet):
    def __init__(self, ospfHeader):
        Packet.__init__(self, ospfHeader)

    def unpack(self, packet):
        length = len(packet)
        format = "!IBBBBHHII"
        neighbors = (length - 20) // 4
        format += neighbors * "I"

        msg = struct.unpack(format, packet)

        self.interfaceId = str(msg[0])
        self.routerPriority = str(msg[1])
        self.options = str(msg[2]) + str(msg[3]) + str(msg[4])
        self.helloInterval = str(msg[5])
        self.routerDeadInterval = str(msg[6])
        self.designatedRouter = ip_converter(msg[7])
        self.backupDesignatedRouter = ip_converter(msg[8])
        self.neighbors = []
        for i in range(0, neighbors):
            self.neighbors.append(ip_converter(msg[9 + i]))

    def process(self, sender, intf, lsdb):
        if self.ospfHeader.routerId not in intf.neighborList:
            addr = sender[0].split('%')[0]
            newNeighbor = neighbors.Neighbor(self.routerDeadInterval, self.ospfHeader.routerId,
                                             self.routerPriority, addr,
                                             self.options, self.designatedRouter,
                                             self.backupDesignatedRouter, self.interfaceId, intf.ospfDb,
                                             intf.areaId, intf)
            intf.neighborList[self.ospfHeader.routerId] = newNeighbor
            if intf.designatedRouter == '0.0.0.0' or intf.backupDesignatedRouter == '0.0.0.0':
                interface.elect_dr_bdr(intf, False)
        else:
            neighbor = intf.neighborList[self.ospfHeader.routerId]
            neighbor.hello_received(intf, self)
            if neighbor.state == 2 and neighbor.neighborDesignatedRouter != '0.0.0.0':
                if intf.routerId in self.neighbors:
                    neighbor.two_way_received(intf)

    def build(self, intf, sendTo, packetBody, packetType):
        packetType = 1
        packet = Packet.build(self, intf, sendTo, packetBody, packetType)
        return packet

    def packet_body(self, intf):
        options = int(0b0010011)
        interfaceID = socket.inet_aton('0.0.0.' + str(intf.intfNumber))
        routerPri = struct.pack('!B', intf.routerPriority)
        options = struct.pack('!H', 0) + struct.pack('!B', options)
        helloInt = struct.pack('!H', intf.helloInterval)
        deadInterval = struct.pack('!H', intf.routerDeadInterval)
        dRouter = socket.inet_aton(intf.designatedRouter)
        bdRouter = socket.inet_aton(intf.backupDesignatedRouter)
        out = [interfaceID, routerPri, options, helloInt, deadInterval, dRouter, bdRouter]
        for neighbor in intf.neighborList.keys():
            out.append(socket.inet_aton(neighbor))
        return out


class DBDescription(Packet):
    def __init__(self, ospfHeader):
        Packet.__init__(self, ospfHeader)

    def unpack(self, packet):
        format = '!BHBHBBI'
        aux = struct.unpack(format, packet[:12])
        self.options = str(aux[2])
        self.interfaceMtu = str(aux[3])
        self.ims = unpack_ims(aux[5])
        self.ddSequence = int(aux[6])
        pktLen = len(packet)
        if pktLen > 12:  # has LSAs
            lsaNbr = (pktLen - 12) / 20
            for i in range(0, lsaNbr):
                start = 12 + 20 * i
                finish = 12 + 20 * (i + 1)
                self.unpack_lsa_header(packet[start:finish])

    def process(self, sender, intf, lsdb):
        neighbor = intf.neighborList[self.ospfHeader.routerId]
        neighbor.db_desc_received(self, intf)

    def build(self, intf, sendTo, packetBody, packetType):
        packetType = 2
        packet = Packet.build(self, intf, sendTo, packetBody, packetType)
        return packet

    def packet_body_simple(self, ims, ddSeq, options):
        filler = struct.pack('!B', 0)
        options = struct.pack('!H', 0) + struct.pack('!B', options)
        interfaceMTU = struct.pack('!H', 1500)
        # filler 8 bytes
        IMS = struct.pack('!B', ims)
        ddSeq = struct.pack('!I', ddSeq)
        out = [filler, options, interfaceMTU, filler, IMS, ddSeq]
        return out

    def packet_body(self, ddseq, ack, intf, neighbor, ims):
        lsdb = intf.get_lsdb()
        flag = ddseq
        options = intf.ospfDb.get_router_options()
        if ddseq == 0:
            if neighbor.ddSequenceNumber == 0:
                ddseq = neighbor.dd_seq_manager()
            else:
                ddseq = neighbor.ddSequenceNumber
        dbpacket = self.packet_body_simple(ims, ddseq, options)
        if (flag == 0) == (not ack):  # initial master/slave election
            return dbpacket
        else:
            LSAs = []
            packetLength = 0
            dbs = lsdb.get_dbs()
            for key in dbs.keys():
                db = dbs[key]
                for lsa in db.values():
                    if key == linkStateDatabase.LINK_LSA:
                        if lsa.interface != intf.intfId:
                            continue
                    pckt, pckLen = lsa.package_lsa(False)
                    LSAs += pckt
                    packetLength += pckLen
            dbs = intf.ospfDb.overlayLsdb.get_dbs()
            for key in dbs.keys():
                db = dbs[key]
                for lsa in db.values():
                    pckt, pckLen = lsa.package_lsa(False)
                    LSAs += pckt
                    packetLength += pckLen

            packetLength += (12 + 16)
            packet = [] + dbpacket + LSAs
            return packet


class LSRequest(Packet):
    def __init__(self, ospfHeader):
        Packet.__init__(self, ospfHeader)
        self.requests = []

    def unpack(self, packet):
        format = '!HHII'
        reqNbr = len(packet) / 12  # 12 = LS Request size
        for i in range(0, reqNbr):
            start = i * 12
            finish = (i + 1) * 12
            aux = struct.unpack(format, packet[start:finish])
            req = Request(int(aux[0]), int(aux[1]), ip_converter(aux[2]), ip_converter(aux[3]))
            self.requests.append(req)

    def process(self, sender, intf, lsdb):
        neighbor = intf.neighborList[self.ospfHeader.routerId]
        neighbor.ls_req_received(self, intf)

    def build(self, intf, sendTo, packetBody, packetType):
        packetType = 3
        packet = Packet.build(self, intf, sendTo, packetBody, packetType)
        return packet

    def packet_body(self, neighbor, lsdb):
        rid = neighbor.neighborId
        LSAs = []
        try:
            for request in lsdb.lsRequests[rid]:
                LSAs += self.ls_request(request)
        except:
            pass
        try:
            for request in lsdb.overlayLsdb.lsRequests[rid]:
                LSAs += self.ls_request(request)
        except:
            addressManagment.print_service('failed to add overlay req')
        return LSAs

    def ls_request(self, request):
        lsType, advRouter, lsid = request.split('-')
        reserved = struct.pack('!H', 0)
        lsType = struct.pack('!H', int(lsType))
        lsid = socket.inet_aton(lsid)
        advRouter = socket.inet_aton(advRouter)
        return [reserved, lsType, lsid, advRouter]


class Request():
    def __init__(self, reserved, lstype, lsid, advrouter):
        self.reserved = reserved
        self.lsType = lstype
        self.linkStateId = lsid
        self.advertisingRouter = advrouter
        self.id = linkStateDatabase.id(lstype, advrouter, lsid)


class LSUpdate(Packet):
    def __init__(self, ospfHeader):
        Packet.__init__(self, ospfHeader)
        self.updates = {}

    def unpack(self, packet):
        nbrUpdates = int(struct.unpack('!I', packet[:4])[0])
        start = 4
        for i in range(0, nbrUpdates):
            lsa = self.unpack_lsa_header(packet[start:start + 20])
            lsaLength = int(lsa.lsaHeader.length)
            lsaStart = start + 20
            lsaFinish = lsaStart + (lsaLength - 20)
            lsa.unpack(packet[lsaStart:lsaFinish])
            self.updates[lsa.lsaHeader.idx] = lsa
            start += lsaLength

    def build(self, intf, sendTo, packetBody, packetType):
        packetType = 4
        packet = Packet.build(self, intf, sendTo, packetBody, packetType)
        return packet

    def packet_body(self, intf, updates):
        lsaNumbr = struct.pack('!I', len(updates))
        LSAs = []
        for update in updates.values():
            lsa = None
            lsaEntry = update
            try:
                lsaHeader = LSAHeader(lsaEntry.lsaHeader.lsAge, lsaEntry.lsaHeader.lsType,
                                      lsaEntry.lsaHeader.linkStateId, lsaEntry.lsaHeader.advertisingRouter,
                                      lsaEntry.lsaHeader.sequenceNumber)
                lsType = lsaHeader.lsType
            except:
                lsaHeader = LSAHeader(lsaEntry.age, lsaEntry.lsType, lsaEntry.lsid, lsaEntry.advRouter,
                                      lsaEntry.sequenceNumber)
                lsType = lsaEntry.lsType
            if lsType == int(0x2001) or lsType == 1:
                lsa = LSAType1(lsaHeader)
            elif lsType == int(0x2002) or lsType == 2:
                lsa = LSAType2(lsaHeader)
            elif lsType == int(0x2003) or lsType == 3:
                lsa = LSAType3(lsaHeader)
            elif lsType == int(0x2008) or lsType == 8:
                lsa = LSAType8(lsaHeader)
            elif lsType == int(0x2009) or lsType == 9:
                lsa = LSAType9(lsaHeader)
            elif lsType == int(0x400a):
                lsa = LSAType10(lsaHeader)
            elif lsType == int(0x400b):
                lsa = LSAType11(lsaHeader)
            elif lsType == int(0x400c):
                lsa = LSAType12(lsaHeader)
            out = lsa.build(lsaEntry, True)
            LSAs += out[0]
        packet = [lsaNumbr] + LSAs
        return packet

    def process(self, sender, intf, lsdb):
        dead = False
        addr = sender[0].split('%')[0]
        self.acknowledge_update(addr, intf)
        dbs = lsdb.ospfDb.get_dbs(lsdb.area)
        update_to_remove = []
        lsas = self.sorter()
        for newlsaidx in lsas:
            newlsa = self.updates[newlsaidx]
            db = dbs[newlsa.lsaHeader.lsType]
            if newlsa.lsaHeader.advertisingRouter == intf.ospfDb.routerId:
                if newlsa.lsaHeader.idx in db:
                    storedlsa = db[newlsa.lsaHeader.idx]
                    if newlsa.lsaHeader.sequenceNumber > storedlsa.sequenceNumber:
                        idx = lsdb.update_self_router_lsa_custom_seq(newlsa.lsaHeader.sequenceNumber + 1)
                        lsdb.update_area_interfaces(idx)
                        update_to_remove.append(newlsa)
                    else:
                        update_to_remove.append(newlsa)
                else:
                    print 'Receive outdated self lsa'
                    addressManagment.print_service(str(db.keys()))
                    addressManagment.print_service(str(newlsa.lsaHeader.idx))
                    print 'Router_Linux: '
                    update_to_remove.append(newlsa)
            else:
                idx = newlsa.lsaHeader.idx
                if newlsa.lsaHeader.lsAge>=3600:
                    dead = True
                if idx in db:
                    if newlsa.lsaHeader.sequenceNumber <= db[idx].sequenceNumber:
                        if newlsa.lsaHeader.sequenceNumber < db[idx].sequenceNumber:
                            print 'Received old lsa: ' + str(idx)
                        update_to_remove.append(newlsa)
                    else:
                        # print '\n\n\tprocessing ' + str(idx) + '\n'
                        newlsa.process(intf, dead)
                else:
                    # print '\n\n\tprocessing ' + str(idx) + '\n'
                    newlsa.process(intf, dead)
        for lsa in update_to_remove:
            del self.updates[lsa.lsaHeader.idx]
        self.forward_updates(intf)

    def acknowledge_update(self, sender, interface):
        lsaHeaders = []
        neighbor = self.ospfHeader.routerId
        neighbor = interface.neighborList[neighbor]
        for lsa in self.updates.values():
            lsaHeaders.append(lsa.lsaHeader)
        neighbor.ls_update_received(lsaHeaders, interface, sender)

    def forward_updates(self, interface):
        updates = []
        ovelrayUpdates = []
        for lsa in self.updates.values():
            if lsa.lsaHeader.lsType == linkStateDatabase.LINK_LSA:
                pass
            elif lsa.lsaHeader.lsType > linkStateDatabase.INTRA_AREA_PREFIX_LSA:
                ovelrayUpdates.append(lsa.lsaHeader.idx)
            else:
                updates.append(lsa.lsaHeader.idx)
        if len(updates):
            for intf in interface.get_lsdb().interfaceList:
                if intf == interface:
                    pass
                else:
                    intf.updateManager.send_multicast_update(intf, updates)
        if len(ovelrayUpdates):
            for intf in interface.ospfDb.interfaceList.values():
                intf.updateManager.send_multicast_update(intf, ovelrayUpdates)

    def sorter(self):
        linklsas = []
        others = []
        for idx in sorted(self.updates.keys()):
            if idx.split('-')[0] == '8':
                linklsas.append(idx)
            else:
                others.append(idx)
        return others + linklsas


class LSAcknowledge(Packet):
    def __init__(self, ospfHeader):
        Packet.__init__(self, ospfHeader)
        self.lsas = {}

    def unpack(self, packet):
        lsaNbr = len(packet) / 20
        for i in range(0, lsaNbr):
            start = i * 20
            finish = start + 20
            lsa = self.unpack_lsa_header(packet[start:finish])
            self.lsas[lsa.lsaHeader.idx] = lsa

    def build(self, intf, sendTo, packetBody, packetType):
        packetType = 5
        packet = Packet.build(self, intf, sendTo, packetBody, packetType)
        return packet

    def packet_body(self, lsas):
        lsaheaders = []
        for header in lsas:
            lsAge = struct.pack('!H', header.lsAge)
            lsType = struct.pack('!H', header.lsType)
            linkStateID = socket.inet_aton(header.linkStateId)
            advertisingRouter = socket.inet_aton(header.advertisingRouter)
            sequenceNumber = struct.pack('!I', header.sequenceNumber)
            checksum = header.checksum
            lsaLength = struct.pack('!H', header.length)
            lsaheaders += [lsAge, lsType, linkStateID, advertisingRouter, sequenceNumber, checksum, lsaLength]
        return lsaheaders

    def process(self, sender, intf, lsdb):
        for lsaheader in self.lsas.values():
            lsaheader = lsaheader.lsaHeader
            idx = lsaheader.idx
            intf.updateManager.acknowledge_received(idx, lsdb, lsaheader.sequenceNumber)
            lsdb = intf.get_lsdb()
            if idx in lsdb.deadLSAs:
                del lsdb.deadLSAs[idx]


def unpack(unpacked_packet):
    ospfHeader = OSPFHeader()
    ospfHeader.unpack(unpacked_packet[:16])
    type = ospfHeader.type
    if type == 1:
        packet = HelloPacket(ospfHeader)
    elif type == 2:
        packet = DBDescription(ospfHeader)
    elif type == 3:
        packet = LSRequest(ospfHeader)
    elif type == 4:
        packet = LSUpdate(ospfHeader)
    elif type == 5:
        packet = LSAcknowledge(ospfHeader)
    else:
        print '\nUnknown packet type'
        return 0
    packet.unpack(unpacked_packet[16:])
    return packet


""" Tools """


def build_db_description_packet(ddSeq, ack, interface, neighbor, ims, sendTo):
    ospfHeader = OSPFHeader()
    ospfHeader.init(2, interface.routerId, interface.areaId)
    packet = DBDescription(ospfHeader)
    packedPacket = packet.packet_body(ddSeq, ack, interface, neighbor, ims)
    packedPacket = packet.build(interface, sendTo, packedPacket, None)
    return packedPacket


def build_ls_request(interface, neighbor):
    ospfHeader = OSPFHeader()
    ospfHeader.init(3, interface.routerId, interface.areaId)
    packet = LSRequest(ospfHeader)
    packedPacket = packet.packet_body(neighbor, interface.get_lsdb())
    packedPacket = packet.build(interface, neighbor.neighborAddress, packedPacket, None)
    return packedPacket


def ls_type(u, s2, s1, lsaFunctionCode):
    return u * 32768 + s2 * 16384 + s1 * 8192 + lsaFunctionCode


def ls_id(uid):
    return uid.split('-')[2]


def check_data(msg, sender, intf):
    sender = sender[0].split('%')[0]
    destination = intf.address
    headerFormat = '!BBHIIHBB'
    header = struct.unpack(headerFormat, msg[:16])
    checksum = struct.pack('!H', header[5])
    length = int(header[2])
    ipv6pHeader = ipv6_pseudoheader(sender, destination, length)
    header = pack(headerFormat, header)
    verifiedChecksum = checksum_ospf([ipv6pHeader, header, [msg[16:]]])
    out = (checksum == verifiedChecksum)
    if not out:
        ipv6pHeader = ipv6_pseudoheader(sender, 'ff02::5', length)
        verifiedChecksum = checksum_ospf([ipv6pHeader, header, [msg[16:]]])
        destination = 'ff02::5'
        out = (checksum == verifiedChecksum)
        if not out:
            print '\nInvalid OSPF checksum!\nIntf: ' + str(intf.intfId)
    return out, destination


def pack(format, list):
    out = []
    for i in range(1, len(format)):
        aux = '!' + format[i]
        out.append(struct.pack(aux, list[i - 1]))
    return out


def checksum_ospf(list):
    packet = ''
    i = 0
    for packetPiece in list:
        j = 0
        for info in packetPiece:
            if i == 1 and j == 5:
                packet += struct.pack('!H', 0)
            else:
                packet += info
            j += 1
        i += 1
    countTo = (int(len(packet) / 2)) * 2
    my_sum = 0
    count = 0

    while count < countTo:
        if sys.byteorder == "little":
            loByte = packet[count]
            hiByte = packet[count + 1]
        else:
            loByte = packet[count + 1]
            hiByte = packet[count]
        my_sum += (ord(hiByte) * 256 + ord(loByte))
        count += 2

    if countTo < len(packet):
        loByte = packet[len(packet) - 1]
        my_sum += ord(loByte)

    my_sum &= 0xffffffff
    my_sum = (my_sum >> 16) + (my_sum & 0xffff)
    my_sum += (my_sum >> 16)
    answer = ~my_sum & 0xffff
    answer = socket.htons(answer)
    return struct.pack("!H", answer)


def ip_converter(input):
    output = struct.pack("!I", input)
    output = socket.inet_ntoa(output)
    return output


def unpack_ims(ims):
    ims = bin(ims)
    out = {}
    if len(ims) == 5:
        out['I'] = ims[2]
        out['M'] = ims[3]
        out['MS'] = ims[4]
    else:
        out['I'] = '0'
        if len(ims) == 4:
            out['M'] = ims[2]
            out['MS'] = ims[3]
        else:
            out['M'] = '0'
            out['MS'] = ims[2]
    return out


def load_size(packet):
    size = 0
    for field in packet:
        size += len(field)
    return size


def packet_builder(list):
    out = ''
    for part in list:
        for info in part:
            out += info
    return out


def build_lsa_options(dc, r, e, v6):
    return 32 * dc + 16 * r + 2 * e + 1 * v6


def fletcher_checksum(list):
    CHKSUM_OFFSET = 16
    packet = ''
    for part in list:
        for info in part:
            packet += info

    packet = packet[:CHKSUM_OFFSET] + '\x00\x00' + packet[CHKSUM_OFFSET + 2:]  # turns chksum to 0
    c0 = c1 = 0
    for char in packet[2:]:  # ignores LS Age
        c0 += ord(char)
        c1 += c0
    c0 %= 255
    c1 %= 255
    x = ((len(packet) - 16 - 1) * c0 - c1) % 255
    if x <= 0:
        x += 255
    y = 510 - c0 - x
    if y > 255:
        y -= 255
    out = chr(x) + chr(y)
    return out


def buildWVEB(w, v, e, b):
    return 8 * w + 4 * v + 2 * e + b * 1


def store_ls_requests(lsdb, packet):  # packet = [header, data]
    rid = packet.ospfHeader.routerId
    overlayLSDB = lsdb.ospfDb.overlayLsdb
    for lsa in packet.lsas.values():
        try:
            lsType = int(lsa.lsType)
        except:
            try:
                if lsa is None:
                    pass
                lsa = lsa.lsaHeader
            except:
                pass
            lsType = int(lsa.lsType)
        if lsType <= int(0x2009):
            lsdb.ls_request(rid, lsa.lsType, lsa.advertisingRouter, lsa.linkStateId, lsa.sequenceNumber)
        else:
            overlayLSDB.ls_request(rid,lsa.lsType,lsa.advertisingRouter,lsa.linkStateId,lsa.sequenceNumber)


def find_ls_index(list, intf):
    out = []
    routerID = intf.routerId
    for entry in list:
        if '-' + routerID + '-' in entry and '-0.0.0.' + str(intf.intfNumber) in entry:
            out.append(entry)
    return out


def link_state_id(lsType, advRouter, lsID):
    return str(lsType) + '-' + advRouter + '-' + str(lsID)
