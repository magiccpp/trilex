#!/bin/bash
# Outbound-only mail relay for Thriauga.
#
# Accepts mail ONLY from the private compose network and delivers straight to
# recipient MX servers, signing with DKIM. It is never published to the host,
# so there is no public SMTP listener and no open-relay exposure.
set -euo pipefail

DOMAIN="${MAIL_DOMAIN:-xiaodong.io}"
HOSTNAME_FQDN="${MAIL_HOSTNAME:-mail.${DOMAIN}}"
SELECTOR="${DKIM_SELECTOR:-thriauga}"
KEYDIR="/dkim/${DOMAIN}"

# --- DKIM key: generated once, then persisted in the mounted volume ---------
mkdir -p "$KEYDIR"
if [ ! -f "${KEYDIR}/${SELECTOR}.private" ]; then
  echo "[dkim] generating 2048-bit key for ${DOMAIN} (selector ${SELECTOR})"
  opendkim-genkey -b 2048 -d "$DOMAIN" -D "$KEYDIR" -s "$SELECTOR" -v
  chown -R opendkim:opendkim "$KEYDIR"
  chmod 600 "${KEYDIR}/${SELECTOR}.private"
fi

echo "=============================================================="
echo " ADD THIS DNS RECORD AT YOUR REGISTRAR, or mail will be spam:"
echo "   host:  ${SELECTOR}._domainkey.${DOMAIN}"
echo "   type:  TXT"
cat "${KEYDIR}/${SELECTOR}.txt" | tr -d '\n\t"()' | sed 's/.*v=DKIM1/   value: v=DKIM1/'
echo ""
echo "   host:  ${DOMAIN}   type: TXT"
echo "   value: v=spf1 ip4:${PUBLIC_IP:-138.128.194.204} -all"
echo "=============================================================="

# --- OpenDKIM ---------------------------------------------------------------
cat > /etc/opendkim.conf <<CONF
Syslog                  yes
UMask                   007
Mode                    s
Canonicalization        relaxed/simple
Domain                  ${DOMAIN}
Selector                ${SELECTOR}
KeyFile                 ${KEYDIR}/${SELECTOR}.private
Socket                  inet:8891@localhost
OversignHeaders         From
CONF

# --- Postfix ----------------------------------------------------------------
postconf -e "myhostname = ${HOSTNAME_FQDN}"
postconf -e "mydomain = ${DOMAIN}"
postconf -e "myorigin = \$mydomain"
postconf -e "inet_interfaces = all"
postconf -e "inet_protocols = ipv4"
# Local delivery is disabled: this relay only sends outward.
postconf -e "mydestination ="
postconf -e "local_transport = error:local delivery disabled"
# ONLY the private compose network may submit. Everything else is rejected,
# which is what stops this from ever acting as an open relay.
postconf -e "mynetworks = 127.0.0.0/8 ${ALLOWED_NET:-172.16.0.0/12}"
postconf -e "smtpd_recipient_restrictions = permit_mynetworks, reject_unauth_destination, reject"
postconf -e "smtpd_relay_restrictions = permit_mynetworks, reject_unauth_destination"
postconf -e "disable_vrfy_command = yes"
postconf -e "smtpd_helo_required = yes"
# Opportunistic TLS outbound: encrypt to any receiver that offers STARTTLS.
postconf -e "smtp_tls_security_level = may"
postconf -e "smtp_tls_loglevel = 1"
postconf -e "smtpd_milters = inet:localhost:8891"
postconf -e "non_smtpd_milters = inet:localhost:8891"
postconf -e "milter_default_action = accept"
postconf -e "maillog_file = /dev/stdout"

if [ -n "${RELAY_HOST:-}" ]; then
  echo "[mail] relaying through ${RELAY_HOST} instead of direct-to-MX"
  postconf -e "relayhost = ${RELAY_HOST}"
  if [ -n "${RELAY_USER:-}" ]; then
    echo "${RELAY_HOST} ${RELAY_USER}:${RELAY_PASS}" > /etc/postfix/sasl_passwd
    postmap /etc/postfix/sasl_passwd && chmod 600 /etc/postfix/sasl_passwd*
    postconf -e "smtp_sasl_auth_enable = yes"
    postconf -e "smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd"
    postconf -e "smtp_sasl_security_options = noanonymous"
    postconf -e "smtp_tls_security_level = encrypt"
  fi
fi

mkdir -p /var/run/opendkim && chown opendkim:opendkim /var/run/opendkim
opendkim -f -x /etc/opendkim.conf &
sleep 1
echo "[mail] postfix starting as ${HOSTNAME_FQDN}"
exec postfix start-fg
