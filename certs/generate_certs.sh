#!/bin/bash
echo "CORDDISS chat Certificate"
echo

if ! command -v openssl &> /dev/null; then
    echo "ERROR: OpenSSL is not installed."
    echo "Please install OpenSSL and try again."
    exit 1
fi

COUNTRY="IN"
STATE="Karnataka"
CITY="Bangalore"
ORG="Pulse-Chat"
UNIT="Development"
CN="localhost"              #can you please put the LOCALHOST IN THIS
DAYS=365                    

echo "Generating private key and self-signed certificate..."
echo

openssl req -x509 -newkey rsa:2048 \
    -keyout server.key \
    -out server.crt \
    -days $DAYS \
    -nodes \
    -subj "/C=$COUNTRY/ST=$STATE/L=$CITY/O=$ORG/OU=$UNIT/CN=$CN"

if [ $? -eq 0 ]; then
    echo
    echo "✓ Certificates generated successfully!"
    echo
    echo "Files created:"
    echo "  - server.key (Private Key)"
    echo "  - server.crt (Certificate)"
    echo
    echo "Certificate valid for $DAYS days"
    echo
    echo "Certificate Information:"
    echo "------------------------"
    openssl x509 -in server.crt -noout -subject -dates
    echo
    
   
    chmod 600 server.key
    chmod 644 server.crt
    echo "Permissions set: server.key - Private, server.crt -Public"
    
else
    echo
    echo "Error generating certificates"
    exit 1
fi

echo "You can now start the server!"
