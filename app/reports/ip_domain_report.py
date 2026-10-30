"""Отчёт по IP-адресу / домену (единый тёмный стиль)."""
from __future__ import annotations

from app.reports.base import esc, render_report
from app.reports.countries import country_label
from app.reports.locales import rl
from app.services.ip_domain import PORT_INFO


def _port_description(port: int) -> str:
    info = PORT_INFO.get(port)
    if info:
        return f"{port} — {info[0]} ({info[1]})"
    return f"{port} — нестандартный порт (назначение требует проверки)"


def build(findings: dict, target: str, language: str = "ru") -> bytes:
    geo = findings.get("geo") or {}
    attacks = findings.get("attacks") or {}
    network = findings.get("network") or {}
    whois = findings.get("whois") or {}
    dns = findings.get("dns") or {}
    subdomains = findings.get("subdomains") or []
    wayback = findings.get("wayback") or {}
    kind = findings.get("kind")
    sections: list[dict] = []

    main_rows = []
    if findings.get("ip"):
        main_rows.append((rl(language, "f_ip"), findings["ip"], True))
    if kind:
        main_rows.append((rl(language, "f_kind"), rl(language, "kind_ip" if kind == "ip" else "kind_domain")))
    if geo.get("country_code") or geo.get("country"):
        main_rows.append(
            (
                rl(language, "f_country"),
                country_label(geo.get("country_code"), language, geo.get("country")),
            )
        )
    if geo.get("region"):
        main_rows.append((rl(language, "f_region"), geo["region"]))
    if geo.get("city"):
        main_rows.append((rl(language, "f_city"), geo["city"]))
    if geo.get("isp"):
        main_rows.append((rl(language, "f_isp"), geo["isp"]))
    if geo.get("org"):
        main_rows.append((rl(language, "f_org"), geo["org"]))
    if geo.get("asn"):
        asn = str(geo["asn"])
        if not asn.upper().startswith("AS"):
            asn = "AS" + asn
        if geo.get("asn_org"):
            asn += f" · {geo['asn_org']}"
        main_rows.append((rl(language, "f_asn"), asn, True))
    if geo.get("timezone"):
        main_rows.append((rl(language, "f_timezone"), geo["timezone"]))
    if geo.get("latitude") is not None and geo.get("longitude") is not None:
        main_rows.append(
            (rl(language, "f_coords"), f"{geo['latitude']}, {geo['longitude']}", True)
        )
    if main_rows:
        sections.append({"id": "main", "title": rl(language, "sec_geo"), "rows": main_rows})

    if attacks:
        rows = []
        if attacks.get("attacks") is not None:
            rows.append((rl(language, "f_attacks"), str(attacks["attacks"]), True))
        if attacks.get("first_seen"):
            rows.append((rl(language, "f_first_seen"), str(attacks["first_seen"])))
        if attacks.get("last_seen"):
            rows.append((rl(language, "f_last_seen"), str(attacks["last_seen"])))
        if attacks.get("threatfeeds"):
            rows.append((rl(language, "f_threatfeeds"), str(attacks["threatfeeds"])))
        if attacks.get("comment"):
            rows.append((rl(language, "f_comment"), str(attacks["comment"])))
        if rows:
            sections.append(
                {"id": "attacks", "title": rl(language, "sec_attacks"), "rows": rows}
            )

    if network.get("ports") or network.get("hostnames") or network.get("tags") or network.get("vulns"):
        rows = []
        if network.get("ports"):
            rows.append(
                (rl(language, "f_ports"), ", ".join(str(p) for p in network["ports"]), True)
            )
        if network.get("hostnames"):
            rows.append((rl(language, "f_hostnames"), ", ".join(network["hostnames"])))
        if network.get("tags"):
            rows.append((rl(language, "f_tags"), ", ".join(network["tags"])))
        if network.get("vulns"):
            rows.append((rl(language, "f_vulns"), ", ".join(str(v) for v in network["vulns"]), True))
        sections.append({"id": "network", "title": rl(language, "sec_network"), "rows": rows})

    ports = network.get("ports") or []
    if ports:
        rows = [(str(port), _port_description(int(port)), True) for port in ports]
        sections.append(
            {
                "id": "ports-summary",
                "title": rl(language, "sec_ports_summary"),
                "rows": rows,
            }
        )

    if kind == "domain":
        if whois:
            rows = []
            if whois.get("handle"):
                rows.append((rl(language, "f_handle"), whois["handle"], True))
            if whois.get("registrar"):
                rows.append((rl(language, "f_registrar"), whois["registrar"]))
            if whois.get("created"):
                rows.append((rl(language, "f_created"), whois["created"], True))
            if whois.get("updated"):
                rows.append((rl(language, "f_updated"), whois["updated"]))
            if whois.get("expires"):
                rows.append((rl(language, "f_expires"), whois["expires"], True))
            if whois.get("status"):
                rows.append((rl(language, "f_status"), ", ".join(str(s) for s in whois["status"])))
            if whois.get("nameservers"):
                rows.append((rl(language, "f_nameservers"), ", ".join(whois["nameservers"])))
            if rows:
                sections.append(
                    {"id": "whois", "title": rl(language, "sec_whois"), "rows": rows}
                )
        else:
            sections.append(
                {
                    "id": "whois",
                    "title": rl(language, "sec_whois"),
                    "note": "Данные WHOIS/RDAP недоступны" if language == "ru" else "WHOIS/RDAP data unavailable",
                }
            )

        if dns:
            rows = []
            for record_type in ("A", "AAAA", "CNAME", "MX", "NS", "TXT"):
                values = dns.get(record_type)
                if values:
                    rows.append((record_type, ", ".join(str(v) for v in values), True))
            if rows:
                sections.append(
                    {"id": "dns", "title": rl(language, "sec_dns"), "rows": rows}
                )
        else:
            sections.append(
                {
                    "id": "dns",
                    "title": rl(language, "sec_dns"),
                    "note": "DNS-записи не найдены" if language == "ru" else "No DNS records found",
                }
            )

        if subdomains:
            rows = [(name, "", True) for name in subdomains]
            sections.append(
                {
                    "id": "subdomains",
                    "title": f"{rl(language, 'sec_subdomains')} · {len(subdomains)}",
                    "rows": rows,
                }
            )
        else:
            sections.append(
                {
                    "id": "subdomains",
                    "title": rl(language, "sec_subdomains"),
                    "note": rl(language, "no_subdomains"),
                }
            )

        links = []
        if wayback.get("available") and wayback.get("url"):
            links.append(wayback["url"])
        for link in wayback.get("links") or []:
            if link not in links:
                links.append(link)
        if links:
            html = "".join(
                f'<div class="note"><a href="{esc(link)}" target="_blank">{esc(link)}</a></div>'
                for link in links
            )
            sections.append(
                {"id": "wayback", "title": rl(language, "sec_wayback"), "html": html}
            )
        else:
            sections.append(
                {
                    "id": "wayback",
                    "title": rl(language, "sec_wayback"),
                    "note": rl(language, "no_snapshot"),
                }
            )

    if not sections:
        sections.append(
            {"id": "empty", "title": rl(language, "sec_empty"), "note": rl(language, "note_empty")}
        )

    stats = []
    if network.get("ports"):
        stats.append((len(network["ports"]), rl(language, "f_ports")))
    if subdomains:
        stats.append((len(subdomains), rl(language, "f_subdomain")))
    if network.get("vulns"):
        stats.append((len(network["vulns"]), "CVE"))
    if attacks.get("attacks") is not None:
        stats.append((attacks["attacks"], rl(language, "f_attacks")))

    return render_report(
        title=rl(language, "ip_title"),
        kind="IP / Domain",
        subtitle=rl(language, "ip_subtitle") + f" · {target}",
        language=language,
        sections=sections,
        stats=stats,
        source="open sources",
        display_name=target,
        avatar_text=(target or "?")[:1].upper(),
    )