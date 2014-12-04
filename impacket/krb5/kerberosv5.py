# Copyright (c) 2003-2014 CORE Security Technologies
#
# This software is provided under under a slightly modified version
# of the Apache Software License. See the accompanying LICENSE file
# for more information.
#
# $Id$
#
# Author: Alberto Solino (beto@coresecurity.com, bethus@gmail.com)
#
# Description:
#   Helper functions for kerberos
#   Just starting, TONS of things to do
#
#

import datetime
import random
import socket
import struct
from pyasn1.codec.der import decoder, encoder
from impacket.krb5.asn1 import AS_REQ, AP_REQ, TGS_REQ, KERB_PA_PAC_REQUEST, KRB_ERROR, PA_ENC_TS_ENC, METHOD_DATA, AS_REP, TGS_REP, EncryptedData, Authenticator, EncASRepPart, EncTGSRepPart, seq_append, seq_set, seq_set_iter, seq_set_dict
from impacket.krb5.types import KerberosTime, Principal, Ticket
from impacket.krb5 import constants
from impacket.krb5.crypto import _RC4, Key
from impacket.smbconnection import SessionError
from impacket.winregistry import hexdump

def sendReceive(data, host, kdcHost):
    if kdcHost is None:
        targetHost = host
    else:
        targetHost = kdcHost

    messageLen = struct.pack('!i', len(data))

    s = socket.socket()
    s.connect((targetHost, 88))
    s.sendall(messageLen + data)

    recvDataLen = struct.unpack('!i', s.recv(4))[0]

    r = s.recv(recvDataLen)
    while len(r) < recvDataLen:
        r += s.recv(recvDataLen-len(r))

    try:
        krbError = KerberosError(packet = decoder.decode(r, asn1Spec = KRB_ERROR())[0])
    except:
        return r

    if krbError.getErrorCode() != constants.ErrorCodes.KDC_ERR_PREAUTH_REQUIRED.value:
        raise krbError

    return r

def getKerberosTGT(clientName, password, domain, lmhash, nthash, kdcHost):
    pacRequest = KERB_PA_PAC_REQUEST()
    pacRequest.setComponentByName('include-pac', True)
    
    encodedPacRequest = encoder.encode(pacRequest)
    paPacRequest = {
        'padata-type': int(constants.PreAuthenticationDataTypes.PA_PAC_REQUEST.value),
        'padata-value': encodedPacRequest
        }

    asReq = AS_REQ()

    serverName = Principal('krbtgt', type=constants.PrincipalNameType.NT_PRINCIPAL.value)  

    asReq.setComponentByName('pvno', 5)
    asReq.setComponentByName('msg-type', int(constants.ApplicationTagNumbers.AS_REQ.value))
    seq_append(asReq, 'padata', paPacRequest)

    reqBody = seq_set(asReq, 'req-body')

    opts = list()
    opts.append( constants.KDCOptions.forwardable.value )
    opts.append( constants.KDCOptions.renewable.value )
    opts.append( constants.KDCOptions.renewable_ok.value )
    opts.append( constants.KDCOptions.canonicalize.value )
    reqBody.setComponentByName('kdc-options', constants.encodeFlags(opts))

    seq_set(reqBody, 'sname', serverName.components_to_asn1)
    seq_set(reqBody, 'cname', clientName.components_to_asn1)

    if domain == '':
        raise 'Empty Domain not allowed in Kerberos'

    reqBody.setComponentByName('realm', domain)

    now = datetime.datetime.utcnow() + datetime.timedelta(days=1)
    reqBody.setComponentByName('till', KerberosTime.to_asn1(now))
    reqBody.setComponentByName('rtime', KerberosTime.to_asn1(now))
    reqBody.setComponentByName('nonce', random.SystemRandom().getrandbits(31))
    seq_set_iter(reqBody, 'etype',
                      (int(constants.EncriptionTypes.des3_cbc_sha1_kd.value),
                       int(constants.EncriptionTypes.rc4_hmac.value)))


    message = encoder.encode(asReq)

    r = sendReceive(message, domain, kdcHost)

    # ToDo: Check the encryption types
    #rep = decoder.decode(r, asn1Spec=KRB_ERROR())[0]
    #error_code = rep.getComponentByName('error-code')
    #e_data = rep.getComponentByName('e-data')
    #method_data = decoder.decode(e_data, asn1Spec=METHOD_DATA())[0]

    # Let's build the timestamp

    timeStamp = PA_ENC_TS_ENC()

    now = datetime.datetime.utcnow() 
    timeStamp.setComponentByName('patimestamp',
                      KerberosTime.to_asn1(now))
    timeStamp.setComponentByName('pausec', now.microsecond)

    # Retrieve the salt from here.. ToDo. no salt usually

    # Encrypt the shyte

    cipher = _RC4()
    key = cipher.string_to_key(password, None, None)
    encodedTimeStamp = encoder.encode(timeStamp)

    # Key Usage 1
    # AS-REQ PA-ENC-TIMESTAMP padata timestamp, encrypted with the 
    # client key (Section 5.2.7.2)
    encriptedTimeStamp = cipher.encrypt(key, 1, encodedTimeStamp, None)

    encryptedData = EncryptedData()
    encryptedData.setComponentByName('etype', int(constants.EncriptionTypes.rc4_hmac.value))
    encryptedData.setComponentByName('cipher', encriptedTimeStamp )
    encodedEncryptedData = encoder.encode(encryptedData)

    paTimeStamp = {
        'padata-type': int(constants.PreAuthenticationDataTypes.PA_ENC_TIMESTAMP.value),
        'padata-value': encodedEncryptedData
    }

    # Now prepare the new AS_REQ again with the PADATA 
    # ToDo: cannot we reuse the previous one?
    asReq = AS_REQ()

    asReq.setComponentByName('pvno', 5)
    asReq.setComponentByName('msg-type', int(constants.ApplicationTagNumbers.AS_REQ.value))
    seq_append(asReq, 'padata', paTimeStamp)
    seq_append(asReq, 'padata', paPacRequest)

    reqBody = seq_set(asReq, 'req-body')

    opts = list()
    opts.append( constants.KDCOptions.forwardable.value )
    opts.append( constants.KDCOptions.renewable.value )
    opts.append( constants.KDCOptions.renewable_ok.value )
    opts.append( constants.KDCOptions.canonicalize.value )
    reqBody.setComponentByName('kdc-options', constants.encodeFlags(opts))

    seq_set(reqBody, 'sname', serverName.components_to_asn1)
    seq_set(reqBody, 'cname', clientName.components_to_asn1)

    reqBody.setComponentByName('realm', domain)

    now = datetime.datetime.utcnow() + datetime.timedelta(days=1)
    reqBody.setComponentByName('till', KerberosTime.to_asn1(now))
    reqBody.setComponentByName('rtime', KerberosTime.to_asn1(now))
    reqBody.setComponentByName('nonce', random.SystemRandom().getrandbits(31))

    seq_set_iter(reqBody, 'etype', ( (int(constants.EncriptionTypes.rc4_hmac.value),)))

    tgt = sendReceive(encoder.encode(asReq), domain, kdcHost) 

    # So, we have the TGT, now extract the new session key and finish

    asRep = decoder.decode(tgt, asn1Spec = AS_REP())[0]
    cipherText = asRep.getComponentByName('enc-part').getComponentByName('cipher')

    # Key Usage 3
    # AS-REP encrypted part (includes TGS session key or
    # application session key), encrypted with the client key
    # (Section 5.4.2)
    plainText = cipher.decrypt(key, 3, str(cipherText))
    encASRepPart = decoder.decode(plainText, asn1Spec = EncASRepPart())[0]

    # Get the session key and the ticket
    # We're assuming the cipher for this session key is the same
    # as the one we used before.
    # ToDo: change this
    sessionKey = Key(cipher.enctype,str(encASRepPart.getComponentByName('key').getComponentByName('keyvalue')))

    # ToDo: Check Nonces!

    return tgt, cipher, sessionKey

def getKerberosTGS(serverName, domain, kdcHost, tgt, cipher, sessionKey):

    # Decode the TGT
    decodedTGT = decoder.decode(tgt, asn1Spec = AS_REP())[0]

    # Extract the ticket from the TGT
    ticket = Ticket()
    ticket.from_asn1(decodedTGT.getComponentByName('ticket'))

    apReq = AP_REQ()
    apReq.setComponentByName('pvno', 5)
    apReq.setComponentByName('msg-type', int(constants.ApplicationTagNumbers.AP_REQ.value))

    opts = list()
    apReq.setComponentByName('ap-options', constants.encodeFlags(opts))
    seq_set(apReq,'ticket', ticket.to_asn1)

    authenticator = Authenticator()
    authenticator.setComponentByName('authenticator-vno',5)
    authenticator.setComponentByName('crealm',str(decodedTGT.getComponentByName('crealm')))

    clientName = Principal()
    clientName.from_asn1( decodedTGT, 'crealm', 'cname')

    seq_set(authenticator, 'cname', clientName.components_to_asn1)
    #authenticator.setComponentByName('cksum',)

    now = datetime.datetime.utcnow()
    authenticator.setComponentByName('cusec', now.microsecond)
    authenticator.setComponentByName('ctime', KerberosTime.to_asn1(now))

    encodedAuthenticator = encoder.encode(authenticator)

    # Key Usage 7
    # TGS-REQ PA-TGS-REQ padata AP-REQ Authenticator (includes
    # TGS authenticator subkey), encrypted with the TGS session
    # key (Section 5.5.1)
    encryptedEncodedAuthenticator = cipher.encrypt(sessionKey, 7, encodedAuthenticator, None)

    encryptedData = {
         'etype': int(constants.EncriptionTypes.rc4_hmac.value),
         'cipher': encryptedEncodedAuthenticator 
    }

    seq_set_dict(apReq, 'authenticator', encryptedData)

    encodedApReq = encoder.encode(apReq)

    paTGSData = {
        'padata-type': int(constants.PreAuthenticationDataTypes.PA_TGS_REQ.value),
        'padata-value': encodedApReq
        }

    tgsReq = TGS_REQ()


    tgsReq.setComponentByName('pvno', 5)
    tgsReq.setComponentByName('msg-type', int(constants.ApplicationTagNumbers.TGS_REQ.value))
    seq_append(tgsReq, 'padata', paTGSData)

    reqBody = seq_set(tgsReq, 'req-body')

    opts = list()
    opts.append( constants.KDCOptions.forwardable.value )
    opts.append( constants.KDCOptions.renewable.value )
    opts.append( constants.KDCOptions.renewable_ok.value )
    opts.append( constants.KDCOptions.canonicalize.value )

    reqBody.setComponentByName('kdc-options', constants.encodeFlags(opts))
    seq_set(reqBody, 'sname', serverName.components_to_asn1)
    reqBody.setComponentByName('realm', str(decodedTGT.getComponentByName('crealm')))

    now = datetime.datetime.utcnow() + datetime.timedelta(days=1)

    reqBody.setComponentByName('till', KerberosTime.to_asn1(now))
    reqBody.setComponentByName('nonce', random.SystemRandom().getrandbits(31))
    seq_set_iter(reqBody, 'etype',
                      (int(constants.EncriptionTypes.des3_cbc_sha1_kd.value),
                       int(constants.EncriptionTypes.rc4_hmac.value)))


    message = encoder.encode(tgsReq)

    r = sendReceive(message, domain, kdcHost)

    # Get the session key

    tgs = decoder.decode(r, asn1Spec = TGS_REP())[0]

    cipherText = tgs.getComponentByName('enc-part').getComponentByName('cipher')

    # Key Usage 3
    # AS-REP encrypted part (includes TGS session key or
    # application session key), encrypted with the client key
    # (Section 5.4.2)
    plainText = cipher.decrypt(sessionKey, 3, str(cipherText))

    encTGSRepPart = decoder.decode(plainText, asn1Spec = EncTGSRepPart())[0]

    newSessionKey = Key(cipher.enctype, str(encTGSRepPart.getComponentByName('key').getComponentByName('keyvalue')))
    
    return r, cipher, newSessionKey

class KerberosError(SessionError):
    """
    This is the exception every client should catch regardless of the underlying
    SMB version used. We'll take care of that. NETBIOS exceptions are NOT included,
    since all SMB versions share the same NETBIOS instances.
    """
    def __init__( self, error = 0, packet=0):
        SessionError.__init__(self)
        self.error = error
        self.packet = packet
        if packet != 0:
            self.error = self.packet.getComponentByName('error-code')
       
    def getErrorCode( self ):
        return self.error

    def getErrorPacket( self ):
        return self.packet

    def getErrorString( self ):
        return constants.ERROR_MESSAGES[self.error]

    def __str__( self ):
        return 'Kerberos SessionError: %s(%s)' % (constants.ERROR_MESSAGES[self.error])
