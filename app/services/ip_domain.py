"""Domain / IP OSINT.

IP:
- геолокация, провайдер, ASN, координаты и часовой пояс;
- проверка сетевой активности/атак;
- открытые порты, хостнеймы и уязвимости;
- понятная расшифровка каждого открытого порта.

Домен:
- WHOIS/RDAP (регистратор, даты, статусы, NS);
- DNS-записи (A, AAAA, MX, NS, TXT, CNAME);
- поддомены из сертификатов и открытых hostsearch-источников;
- ссылки на архивные копии.

Все запросы идут через единый HTTP-клиент с пулом соединений и DNS-кэшем.
Сбой одного источника не ломает отчёт.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
from urllib.parse import urlparse

from app.config import settings
from app.services.http import fetch_text, gather_with_deadline, get_json
from app.services.profiler import profiled

log = logging.getLogger(__name__)

_DOMAIN_RE = re.compile(r"^(?!-)(?:[a-z0-9-]{1,63}\.)+(?:[a-z]{2,24}|xn--[a-z0-9-]{2,30})$", re.I)
_MAX_SUBDOMAINS = 40

# Понятная расшифровка портов для отчёта.
PORT_INFO: dict[int, tuple[str, str]] = {
    20: ("FTP-data", "передача данных FTP"),
    21: ("FTP", "файловый сервер"),
    22: ("SSH", "удалённый доступ к серверу"),
    23: ("Telnet", "незащищённый удалённый доступ"),
    25: ("SMTP", "отправка почты"),
    53: ("DNS", "служба доменных имён"),
    67: ("DHCP", "выдача сетевых настроек"),
    69: ("TFTP", "простой файловый сервер"),
    80: ("HTTP", "веб-сервер"),
    110: ("POP3", "получение почты"),
    111: ("RPCbind", "служба RPC"),
    123: ("NTP", "синхронизация времени"),
    135: ("MSRPC", "службы Windows"),
    137: ("NetBIOS", "службы Windows"),
    138: ("NetBIOS", "службы Windows"),
    139: ("NetBIOS/SMB", "общий доступ Windows"),
    143: ("IMAP", "получение почты"),
    161: ("SNMP", "мониторинг оборудования"),
    389: ("LDAP", "каталог пользователей"),
    443: ("HTTPS", "защищённый веб-сервер"),
    445: ("SMB", "общие папки Windows"),
    465: ("SMTPS", "защищённая отправка почты"),
    500: ("IKE/IPsec", "VPN-соединения"),
    514: ("Syslog", "журналирование"),
    587: ("SMTP", "отправка почты"),
    631: ("IPP", "сетевые принтеры"),
    636: ("LDAPS", "защищённый каталог"),
    873: ("rsync", "синхронизация файлов"),
    990: ("FTPS", "защищённый FTP"),
    993: ("IMAPS", "защищённая почта"),
    995: ("POP3S", "защищённая почта"),
    1080: ("SOCKS", "прокси-сервер"),
    1194: ("OpenVPN", "VPN-соединения"),
    1433: ("MSSQL", "база данных"),
    1521: ("Oracle", "база данных"),
    1723: ("PPTP", "VPN-соединения"),
    2049: ("NFS", "сетевые файлы"),
    2181: ("ZooKeeper", "координация сервисов"),
    2375: ("Docker", "управление контейнерами"),
    2376: ("Docker TLS", "защищённое управление контейнерами"),
    3000: ("Dev-сервер", "веб-приложение или API"),
    3306: ("MySQL", "база данных"),
    3389: ("RDP", "удалённый рабочий стол"),
    4443: ("HTTPS-alt", "веб-сервер или API"),
    5000: ("Dev-сервер", "веб-приложение или API"),
    5432: ("PostgreSQL", "база данных"),
    5601: ("Kibana", "панель поиска по логам"),
    5672: ("RabbitMQ", "очередь сообщений"),
    5900: ("VNC", "удалённый рабочий стол"),
    5984: ("CouchDB", "база данных"),
    6379: ("Redis", "кэш или база данных"),
    6443: ("Kubernetes API", "управление кластером"),
    7001: ("WebLogic", "сервер приложений"),
    8000: ("HTTP-alt", "веб-приложение или API"),
    8080: ("HTTP-alt", "веб-приложение или прокси"),
    8081: ("HTTP-alt", "веб-приложение или прокси"),
    8090: ("HTTP-alt", "веб-приложение или API"),
    8443: ("HTTPS-alt", "веб-приложение или API"),
    8888: ("HTTP-alt", "веб-приложение или панель"),
    9000: ("HTTP-alt", "веб-приложение или API"),
    9090: ("HTTP-alt", "веб-приложение или API"),
    9200: ("Elasticsearch", "поисковый движок"),
    9300: ("Elasticsearch", "кластерный обмен"),
    10000: ("Webmin", "панель управления сервером"),
    11211: ("Memcached", "кэш в памяти"),
    27017: ("MongoDB", "база данных"),
    27018: ("MongoDB", "база данных"),
    50000: ("SAP", "корпоративное приложение"),
}


def _to_ascii_domain(host: str) -> str | None:
    """Юникод-домен (в т.ч. .рф) -> punycode; punycode принимаем как есть."""
    try:
        import idna as _idna  # type: ignore
    except ImportError:  # fallback на встроенный codec
        _idna = None
    host = host.strip().strip(".").lower()
    if not host:
        return None
    try:
        if _idna is not None:
            return _idna.encode(host).decode("ascii")
        return host.encode("idna").decode("ascii")
    except Exception:  # noqa: BLE001
        if host.isascii() and "xn--" in host:
            return host
        return None


def _is_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_reserved
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
    )


def detect_kind(value: str) -> tuple[str, str] | None:
    """Возвращает ("ip"|"domain", normalized) или None.

    Поддержка IPv4/IPv6 (в т.ч. в URL), IDN (.рф, xn--*), TLD с цифрами и
    дефисом. Приватные/зарезервированные IP возвращают ("ip_private", value).
    """
    raw = (value or "").strip()
    if not raw:
        return None

    candidate = raw
    if "://" in candidate:
        parsed = urlparse(candidate)
        candidate = parsed.hostname or ""
    # IPv6 в скобках [::1]:443
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1:candidate.index("]")]
    else:
        candidate = candidate.split("/")[0].split("?", 1)[0].strip()

    # IP (v4/v6)
    try:
        ip = ipaddress.ip_address(candidate.strip("[]"))
        if not _is_public_ip(ip):
            return ("ip_private", str(ip))
        return ("ip", str(ip))
    except ValueError:
        pass

    host = candidate.split(":")[0].strip(".").lower() if candidate.count(":") == 1 else candidate.strip(".")
    ascii_host = _to_ascii_domain(host)
    if not ascii_host:
        return None
    if _DOMAIN_RE.match(ascii_host):
        return ("domain", ascii_host)
    return None


async def _resolve(domain: str) -> str | None:
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(domain, None),
            timeout=min(2.0, settings.search_deadline),
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("DNS resolve failed for %s: %s", domain, type(exc).__name__)
        return None
    for info in infos:
        try:
            addr = info[4][0]
            ipaddress.ip_address(addr)
            return addr
        except (ValueError, IndexError):
            continue
    return None


def _parse_geo(data: object) -> dict:
    if not isinstance(data, dict) or data.get("success") is False or data.get("error"):
        return {}
    conn = data.get("connection") if isinstance(data.get("connection"), dict) else {}
    flag = data.get("flag") if isinstance(data.get("flag"), dict) else {}
    timezone = data.get("timezone")
    code = data.get("country_code") or data.get("country")
    country_name = data.get("country_name")
    result = {
        "country": country_name or data.get("country"),
        "country_code": code,
        "city": data.get("city"),
        "region": data.get("region"),
        "postal": data.get("postal"),
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": timezone.get("id") if isinstance(timezone, dict) else timezone,
        "isp": conn.get("isp") or data.get("org"),
        "org": conn.get("org") or data.get("org"),
        "asn": conn.get("asn") or data.get("asn"),
        "asn_org": conn.get("domain") or data.get("org"),
        "flag": flag.get("emoji"),
        "type": data.get("type"),
    }
    return {k: v for k, v in result.items() if v not in (None, "", [])}


def _parse_isc(data: object) -> dict:
    """Разбирает ответ проверки сетевой активности (JSON)."""
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return {}
    if isinstance(data.get("ip"), dict):
        data = data["ip"]
    feeds = data.get("threatfeeds")
    result = {
        "attacks": data.get("attacks"),
        "as": data.get("as") or data.get("asname"),
        "country": data.get("country"),
        "city": data.get("city"),
        "region": data.get("region"),
        "hostname": data.get("hostname"),
        "first_seen": data.get("mindate"),
        "last_seen": data.get("maxdate"),
        "comment": data.get("comment"),
        "threatfeeds": list(feeds) if isinstance(feeds, dict) else feeds,
    }
    return {k: v for k, v in result.items() if v not in (None, "", [], {})}


def _parse_network(data: object) -> dict:
    if not isinstance(data, dict):
        return {}
    return {
        "ports": sorted({int(p) for p in (data.get("ports") or []) if str(p).isdigit()}),
        "hostnames": data.get("hostnames") or [],
        "cpes": data.get("cpes") or [],
        "tags": data.get("tags") or [],
        "vulns": list(data.get("vulns") or []),
    }


def _parse_crt(data: object) -> list[str]:
    if not isinstance(data, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        raw = item.get("name_value") or item.get("common_name") or ""
        for name in str(raw).splitlines():
            name = name.strip().lower().lstrip("*.").strip()
            if not name or name in seen or not _DOMAIN_RE.match(name):
                continue
            seen.add(name)
            names.append(name)
            if len(names) >= _MAX_SUBDOMAINS:
                return names
    return names


def _parse_hostsearch(text: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        name = line.split(",", 1)[0].strip().lower().lstrip("*.")
        if not name or name in seen or not _DOMAIN_RE.match(name):
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= _MAX_SUBDOMAINS:
            break
    return names


def _parse_wayback(data: object, domain: str) -> dict:
    result: dict = {
        "links": [
            f"https://web.archive.org/web/*/{domain}",
            f"https://web.archive.org/web/2024/{domain}",
        ]
    }
    if isinstance(data, dict):
        snapshots = data.get("archived_snapshots")
        snap = snapshots.get("closest") if isinstance(snapshots, dict) else None
        if isinstance(snap, dict):
            result.update(
                {
                    "available": bool(snap.get("available")),
                    "url": snap.get("url"),
                    "timestamp": snap.get("timestamp"),
                }
            )
    return result


def _parse_rdap(data: object) -> dict:
    if not isinstance(data, dict):
        return {}
    events: dict[str, str] = {}
    for event in data.get("events") or []:
        if not isinstance(event, dict):
            continue
        action = str(event.get("eventAction") or "").lower()
        date = event.get("eventDate")
        if action and date:
            events[action] = str(date)

    registrar = None
    for entity in data.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        roles = [str(r).lower() for r in (entity.get("roles") or [])]
        if "registrar" not in roles:
            continue
        vcard = entity.get("vcardArray")
        if isinstance(vcard, list) and len(vcard) > 1:
            for item in vcard[1]:
                if isinstance(item, list) and len(item) >= 4 and item[0] == "fn":
                    registrar = str(item[3])
                    break
        if not registrar and entity.get("handle"):
            registrar = str(entity["handle"])
        if registrar:
            break

    nameservers = []
    for ns in data.get("nameservers") or []:
        if isinstance(ns, dict) and ns.get("ldhName"):
            nameservers.append(str(ns["ldhName"]).lower())

    return {
        "handle": data.get("handle"),
        "registrar": registrar,
        "created": events.get("registration"),
        "updated": events.get("last changed") or events.get("last update of rdap database"),
        "expires": events.get("expiration"),
        "status": data.get("status") or [],
        "nameservers": list(dict.fromkeys(nameservers)),
    }


def _parse_dns(data: object, record_type: str) -> list[str]:
    if not isinstance(data, dict):
        return []
    values: list[str] = []
    for answer in data.get("Answer") or []:
        if not isinstance(answer, dict):
            continue
        value = answer.get("data")
        if value and value not in values:
            values.append(str(value))
    return values


def _ports_summary(ports: list[int]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for port in ports:
        info = PORT_INFO.get(port)
        if info:
            rows.append((str(port), f"{info[0]} — {info[1]}"))
        else:
            rows.append((str(port), "нестандартный порт — назначение требует проверки"))
    return rows


async def _fetch_geo(ip: str) -> dict:
    primary, fallback = await gather_with_deadline(
        get_json(f"https://ipapi.co/{ip}/json/", ttl=600),
        get_json(f"https://ipwho.is/{ip}", ttl=600),
    )
    for result in (primary, fallback):
        if isinstance(result, tuple) and result[0] is not None:
            geo = _parse_geo(result[0])
            if geo:
                return geo
    return {}


async def _fetch_dns(domain: str) -> dict:
    types = ("A", "AAAA", "MX", "NS", "TXT", "CNAME")
    results = await gather_with_deadline(
        *(
            get_json(
                "https://dns.google/resolve",
                params={"name": domain, "type": record_type},
                ttl=600,
            )
            for record_type in types
        )
    )
    dns: dict[str, list[str]] = {}
    for record_type, result in zip(types, results):
        if isinstance(result, tuple) and result[0] is not None:
            values = _parse_dns(result[0], record_type)
            if values:
                dns[record_type] = values
    return dns


async def _fetch_crt(domain: str):
    """Сертификаты отдают большой JSON: лимит чтения и парсинг вне event loop."""
    status, text = await fetch_text(
        "https://crt.sh/",
        params={"q": f"%.{domain}", "output": "json"},
        timeout=settings.search_request_timeout,
        max_bytes=2 * 1024 * 1024,
    )
    if status != 200 or not text:
        return None, f"http_{status}" if status else "network"
    try:
        return await asyncio.to_thread(json.loads, text), None
    except ValueError:
        return None, "not_json"


async def _fetch_hostsearch(domain: str) -> list[str]:
    status, text = await fetch_text(
        "https://api.hackertarget.com/hostsearch/",
        params={"q": domain},
        timeout=settings.search_request_timeout,
        max_bytes=512 * 1024,
    )
    if status != 200 or not text:
        return []
    return _parse_hostsearch(text)


@profiled("service.ip_domain")
async def analyze(target: str) -> dict:
    result: dict = {
        "target": target,
        "kind": None,
        "ip": None,
        "error": None,
        "geo": {},
        "attacks": {},
        "network": {},
        "whois": {},
        "dns": {},
        "subdomains": [],
        "wayback": {},
        "source": "open sources",
    }

    detected = detect_kind(target)
    if not detected:
        result["error"] = "invalid_input"
        return result

    loop = asyncio.get_running_loop()
    started = loop.time()
    kind, value = detected
    result["kind"] = kind
    ip = value
    if kind == "domain":
        ip = await _resolve(value)
        if not ip:
            result["error"] = "not_found"
            return result
    result["ip"] = ip

    tasks: list = [
        _fetch_geo(ip),
        get_json(f"https://isc.sans.edu/api/ip/{ip}?json", ttl=600),
        get_json(f"https://internetdb.shodan.io/{ip}", ttl=600),
    ]
    domain_tasks: list = []
    if kind == "domain":
        domain_tasks = [
            get_json(f"https://rdap.org/domain/{value}", ttl=1800),
            _fetch_dns(value),
            _fetch_crt(value),
            _fetch_hostsearch(value),
            get_json(
                "https://archive.org/wayback/available",
                params={"url": value},
                ttl=1800,
            ),
        ]
        tasks.extend(domain_tasks)

    remaining = max(1.0, settings.search_deadline - (loop.time() - started))
    gathered = await gather_with_deadline(*tasks, timeout=remaining)

    geo = gathered[0]
    if isinstance(geo, dict):
        result["geo"] = geo

    attacks = gathered[1]
    if isinstance(attacks, tuple) and attacks[0] is not None:
        result["attacks"] = _parse_isc(attacks[0])

    network = gathered[2]
    if isinstance(network, tuple) and network[0] is not None:
        result["network"] = _parse_network(network[0])

    if kind == "domain":
        rdap = gathered[3]
        if isinstance(rdap, tuple) and rdap[0] is not None:
            result["whois"] = _parse_rdap(rdap[0])

        dns = gathered[4]
        if isinstance(dns, dict):
            result["dns"] = dns

        crt = gathered[5]
        subdomains: list[str] = []
        if isinstance(crt, tuple) and crt[0] is not None:
            subdomains.extend(_parse_crt(crt[0]))

        hosts = gathered[6]
        if isinstance(hosts, list):
            subdomains.extend(hosts)

        result["subdomains"] = list(dict.fromkeys(subdomains))[:_MAX_SUBDOMAINS]

        wayback = gathered[7]
        if isinstance(wayback, tuple):
            result["wayback"] = _parse_wayback(wayback[0], value)

    if (
        not result["geo"]
        and not result["attacks"]
        and not result["network"]
        and not result["whois"]
        and not result["dns"]
        and not result["subdomains"]
    ):
        result["error"] = "unavailable"
    return result