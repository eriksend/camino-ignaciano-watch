#!/usr/bin/env python3
"""Process new_items.json into findings, appending to findings.json."""
import json, hashlib, os
from datetime import datetime, timezone

DETECTED_AT = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# --- hand-scored findings for this run ---
new_findings_raw = [
    {
        "source_name": "Camino Ignaciano — Inicio (ES)",
        "url": "https://caminoignaciano.org/",
        "region": "whole",
        "tier": "official",
        "lang": "es",
        "title": "Camino Ignaciano Official Site — 2029 Jubilee Countdown and Stage Overview",
        "summary_en": "The official Camino Ignaciano website highlights the upcoming 2029 Jubilee and lists all 29 stages from Loyola to Manresa, plus an Italy extension to Rome. Recent news includes a 2025 Progress Report and updates about a Spain-to-Italy walk.",
        "relevance": 42,
    },
    {
        "source_name": "Camino Ignaciano — Home (EN)",
        "url": "https://caminoignaciano.org/en/",
        "region": "whole",
        "tier": "official",
        "lang": "en",
        "title": "Ignatian Way Official English Site — 2029 Jubilee and Complete Stage List",
        "summary_en": "The English-language official Camino Ignaciano website promotes the 2029 Jubilee and presents all 29 stages from Loyola to Manresa, with an additional Italy extension totaling over 900 km. It lists recent news including a 2025 Progress Report.",
        "relevance": 35,
    },
    {
        "source_name": "Camino Ignaciano — Alojamiento (accommodation list)",
        "url": "https://caminoignaciano.org/alojamiento/",
        "region": "whole",
        "tier": "official",
        "lang": "es",
        "title": "Official Accommodation List — Full Route from Agoncillo to Zaragoza",
        "summary_en": "The official Camino Ignaciano website provides a comprehensive alphabetical accommodation list covering the entire route, with phone numbers, addresses, and pilgrim discount notes. Entries include pilgrim hostels, pensiones, rural houses, and municipal shelters; several hostels note pilgrim-credential discounts or free beds.",
        "relevance": 100,
    },
    {
        "source_name": "Camino Ignaciano — Aspectos prácticos",
        "url": "https://caminoignaciano.org/en/practicalities/",
        "region": "whole",
        "tier": "official",
        "lang": "en",
        "title": "Camino Ignaciano Practicalities — Costs, Best Seasons, and Equipment Guide",
        "summary_en": "The official guide recommends spring and fall as the best seasons and warns of summer heat up to 40°C in Zaragoza and winter snow between Loyola and Logroño. Daily costs run €45–65, with pilgrim-refuge beds at €15–20, hostel rooms at €30–50, and food budgeted at €20–30 per day.",
        "relevance": 66,
    },
    {
        "source_name": "Camino Ignaciano — Credenciales / certificados",
        "url": "https://caminoignaciano.org/en/tips-for-pilgrims/",
        "region": "whole",
        "tier": "official",
        "lang": "en",
        "title": "Pilgrim Credential Required; Monegros Desert Stretch Has Very Limited Accommodation",
        "summary_en": "Pilgrims must obtain a credential from an official organization before departure to access pilgrim hostels. The stretch between Zaragoza and Fraga through the Monegros desert has almost no accommodation and is the most remote and demanding section of the entire camino.",
        "relevance": 66,
    },
    {
        "source_name": "Jesuit Sources — official guide editions",
        "url": "https://jesuitsources.bc.edu/?s=camino+ignaciano",
        "region": "whole",
        "tier": "guide",
        "lang": "en",
        "title": "New Updated Edition of Official Camino Ignaciano Guide Available at Jesuit Sources",
        "summary_en": "Jesuit Sources bookstore lists a new updated edition of the Official Guide to the Camino Ignaciano for $24.95. A 'Journey with Ignatius Pilgrim Pack' is also available at $116.85.",
        "relevance": 70,
    },
    {
        "source_name": "Chris Lowney — books / Ignatian Way",
        "url": "http://chrislowney.com/wp/books/",
        "region": "whole",
        "tier": "guide",
        "lang": "en",
        "title": "Chris Lowney Books Page — Generic Listing, No Camino-Specific Content",
        "summary_en": "The Chris Lowney website bookshelf page contains only generic social media links and no Camino Ignaciano-specific information.",
        "relevance": 4,
    },
    {
        "source_name": "Gronze — Camino Ignaciano (General)",
        "url": "https://www.gronze.com/foros/general/camino-ignaciano-2",
        "region": "whole",
        "tier": "forum",
        "lang": "es",
        "title": "Gronze Forum: Camino Ignaciano Trail Tips, Accommodation, and Credentials",
        "summary_en": "Forum pilgrims report the Camino Ignaciano is very uncrowded — typically only 1–2 other pilgrims encountered on the entire route. Stage 2 (Zumárraga to Arántzazu, 19–21 km) is the most demanding and requires carrying water and food. Pilgrim credentials can be purchased at Loyola/Azpeitia and municipalities offer free swimming pools to credentialed pilgrims in summer.",
        "relevance": 44,
    },
    {
        "source_name": "Gronze — Información Camino Ignaciano (Otros Caminos)",
        "url": "https://www.gronze.com/foros/otros-caminos/informacion-sobre-camino-ignaciano",
        "region": "whole",
        "tier": "forum",
        "lang": "es",
        "title": "Gronze 2019: Camino Ignaciano Shares Sections with Camino del Ebro; Credentials Accepted",
        "summary_en": "A 2019 forum thread confirms the Camino Ignaciano overlaps with the Camino del Ebro and Camino Catalán from Navarrete onwards. The Camino Ignaciano credential (obtained at Loiola) is accepted at Camino de Santiago pilgrim hostels along the shared stretch.",
        "relevance": 20,
    },
    {
        "source_name": "Nos vamos de ruta — Camino Ignaciano",
        "url": "https://nosvamosderuta.es/camino-ignaciano/",
        "region": "whole",
        "tier": "blog",
        "lang": "es",
        "title": "Camino Ignaciano 2026 Guide Overview — 650 km, 27 Stages, Five Regions",
        "summary_en": "A February 2025 guide describes the Camino Ignaciano as a 650 km, 27-stage route across five autonomous communities (País Vasco, La Rioja, Navarra, Aragón, Cataluña). It recommends the official website and mobile app for up-to-date stage, accommodation, and point-of-interest information.",
        "relevance": 16,
    },
    {
        "source_name": "Coge tu mochila — Camino Ignaciano",
        "url": "https://cogetumochila.com/blog/camino-ignaciano-quinto-aniversario-ruta-peregrinacion/",
        "region": "whole",
        "tier": "blog",
        "lang": "es",
        "title": "Camino Ignaciano 500th Anniversary Overview — Only 2,500 Pilgrims in 2019",
        "summary_en": "This anniversary article recaps the 675 km, 27-stage Camino Ignaciano and notes that in 2019 only 2,500 pilgrims reached Manresa — a fraction of Camino de Santiago numbers — making it a very quiet route. The article covers the 2022 Ignatius 500 commemorative jubilee year.",
        "relevance": 14,
    },
    {
        "source_name": "Fermín Lopetegui — Guía (blog)",
        "url": "http://ferminlopetegui.blogspot.com/",
        "region": "basque",
        "tier": "blog",
        "lang": "es",
        "title": "Camino Ignaciano Catalonia Stage Notes — Water Warnings and Verdú Detour Explained",
        "summary_en": "Fermín Lopetegui (recognized first pilgrim of the Camino Ignaciano, March 2012) provides detailed stage waypoint distances for the Catalonia section. He notes that stages 22–23 cross long agricultural stretches with few services and warns pilgrims to carry water in hot weather; the detour to Verdú (birthplace of Jesuit San Pedro Claver) marks the start of the final 100 km.",
        "relevance": 32,
    },
    {
        "source_name": "Marly Camino — Ignatian Camino",
        "url": "https://marlycamino.com/camino/ignatian-camino/",
        "region": "whole",
        "tier": "tour",
        "lang": "en",
        "title": "Marly Camino Guided Ignatian Way Tour — 13 Days, 141 km, Pamplona to Barcelona",
        "summary_en": "Marly Camino offers a 13-day guided Ignatian Camino covering 141 km from Pamplona to Barcelona at medium difficulty. The tour includes private hotel accommodation with en-suite bathrooms, all breakfasts and 5 dinners, a support vehicle, and caters to special diets.",
        "relevance": 18,
    },
    {
        "source_name": "Camino Ignaciano MTB",
        "url": "https://www.caminoignacianomtb.com/",
        "region": "whole",
        "tier": "tour",
        "lang": "es",
        "title": "Camino Ignaciano MTB Guided Cycling Tours — 9 to 12 Days with Luggage Transfer",
        "summary_en": "GuiesBtt.cat offers guided mountain bike tours of the Camino Ignaciano in 9-, 10-, 11-, and 12-day formats with luggage transfers and half-board accommodation. The first stage is 67 km with 1,800 m elevation and the operators note it is very demanding in wet or winter conditions.",
        "relevance": 10,
    },
    {
        "source_name": "Cova de Sant Ignasi — Notícies (Manresa)",
        "url": "https://www.covamanresa.cat/en/actualitat",
        "region": "catalonia",
        "tier": "official",
        "lang": "en",
        "title": "Cova de Sant Ignasi 2026 Events — Montserrat Vigil, Pilgrimage to Manresa, Online Symposium",
        "summary_en": "The Sanctuary of the Cave of Saint Ignatius in Manresa has announced a Jesuit-organized overnight vigil at Montserrat followed by a pilgrimage to Manresa on March 20–21, 2026 (retracing Ignatius's 1522 arrival). An International Online Symposium on Spiritual Exercises runs June 15–19, 2026; the Chapel of Santa Llúcia is undergoing comprehensive restoration funded by the Barcelona Provincial Council.",
        "relevance": 55,
    },
    {
        "source_name": "Cova de Sant Ignasi — Visiting hours / activities",
        "url": "https://www.covamanresa.cat/en/sanctuary-activities",
        "region": "catalonia",
        "tier": "official",
        "lang": "en",
        "title": "Cova de Sant Ignasi Visiting Hours — Spring/Summer 10am–1pm and 4pm–7pm",
        "summary_en": "The Cave of Saint Ignatius in Manresa is open year-round (closed only Easter Monday and December 26). Spring/summer hours (March 1–October 31) are 10am–1pm and 4pm–7pm Monday–Saturday; Sundays 10–11am only. Daily Mass at 12:45pm (Monday–Saturday) runs from April 8th, excluding July 1–September 11.",
        "relevance": 59,
    },
    {
        "source_name": "Històries Manresanes — Camí Ignasià (blog)",
        "url": "https://www.historiesmanresanes.cat/",
        "region": "catalonia",
        "tier": "blog",
        "lang": "ca",
        "title": "Manresa's Eight Medieval City Gates — Remnants Pilgrims Can Visit Today",
        "summary_en": "A May 2026 Catalan article describes Manresa's medieval fortifications, including eight city gates: Sobrerroca, Sant Domènec, Santa Llúcia, Sant Miquel, Valldaura, Sant Francesc, Galceran Andreu, and les Piques. Pilgrims arriving in Manresa can still visit preserved remnants including the Torre de Sobrerroca, walls at Plaça Europa, and the Muralla del Carme.",
        "relevance": 14,
    },
    {
        "source_name": "Gipuzkoa — Camino Ignaciano (Aisialdi)",
        "url": "https://www.kulturweb.com/adm/ficha.asp?tipoficha=1&id=160137&que=557&L_Id=59&idioma=es",
        "region": "basque",
        "tier": "town",
        "lang": "es",
        "title": "Camino Ignaciano in Gipuzkoa — 7 Stages, 150 km, Most Rugged Section of the Route",
        "summary_en": "Gipuzkoa's tourism guide lists the Camino Ignaciano as a 2026 year-round activity starting from the Santuario de Loiola in Azpeitia. The Basque section covers 7 stages and 150 km across six comarcas and is described as the most rugged stretch of the entire pilgrimage.",
        "relevance": 18,
    },
    {
        "source_name": "Google News — \"Camino Ignaciano\" (ES)",
        "url": "https://news.google.com/rss/articles/CBMi1gFBVV95cUxPdVB6OW5kb0R5UGdCVGtFaHNSS0Q0ekRsLXFUUjc1Yk43ZDFMdGQ3cVA2UTMxLUpYOVdIdXJTYTRMaHoxSFQ1YnNaRkdydUt3U3JyYzU5djhSMmtWaHhXTFo5RnNCWXNUVnJzcFBTUDdjb0RfMkVDS2RfUFlWOWZrbUcxWlZ5NnhvWDVXWFY4RW5oWjBxRmtncnQyVmhwT3Rja19NUnZxVGZqYU5HcF9rQk9hbHF2MjdqbTVGRllrQjdFS0JqaXFGNjNhUmRlMVlHSEt4WS13?oc=5",
        "region": "whole",
        "tier": "social",
        "lang": "es",
        "title": "Spanish Minister Visits Manresa: €2.7M Camino Ignaciano Rehabilitation Project",
        "summary_en": "Spain's Minister of Industry and Tourism Jordi Hereu visited Manresa to review the Camino Ignaciano Rehabilitation Project, which has been allocated €2.7 million in funding. The project involves restoration and infrastructure improvements along the route.",
        "relevance": 68,
    },
    {
        "source_name": "Google News — \"Ignatian Camino\" (EN)",
        "url": "https://news.google.com/rss/articles/CBMinwFBVV95cUxQcXBlWUJNTVRzQ1hieTVGZzJqT0d4M2c2dkNXQVFGdkw2cHhmdlQtUmMzdzdPSjFrYVEwRlB4VnpuTEtyZVNjZ3BLOXFXcEdfVkMyeXNDVWpKZ3RzZ2VySmpUVndWcXJWalZtcGxPVnJ3YW1oclRZNGxlUmVSQkluMzlobUlhcGlTSS1BQ0FLWk5fSWN3akZtaTFlUGFBczjSAZ8BQVVfeXFMUHFwZVlCTU1Uc0NYYnk1Rmcyak9HeDNnNnZDV0FRRnZMNnB4ZnZULVJjM3c3T0oxa2FRMEZQeFZ6bkxLcmVTY2dwSzlxV3BHX1ZDMnlzQ1VqSmd0c2dlckpqVFZ3VnFyVmpWbXBsT1Zyd2FtaHJUWTRsZVJlUkJJbjM5aG1JYXBpU0ktQUNBS1pOX0ljd2pGbWkxZVBhQXM4?oc=5",
        "region": "whole",
        "tier": "social",
        "lang": "es",
        "title": "Documentary 'El Camino Ignaciano' to Premiere at Vatican Film Library",
        "summary_en": "According to Vida Nueva, a documentary titled 'El Camino Ignaciano' has begun a run at the Filmoteca Vaticana (Vatican Film Library). No further details about content or a broader screening schedule are available in the news snippet.",
        "relevance": 14,
    },
]

STATE_DIR = os.path.join(os.path.dirname(__file__), "..", "state")
FINDINGS_PATH = os.path.join(STATE_DIR, "findings.json")
NEW_ITEMS_PATH = os.path.join(STATE_DIR, "new_items.json")

def make_id(url: str, title: str) -> str:
    return hashlib.sha1((url + title).encode()).hexdigest()[:16]

# Load existing findings
if os.path.exists(FINDINGS_PATH):
    with open(FINDINGS_PATH) as f:
        findings = json.load(f)
else:
    findings = []

# Mark all existing as not new
for f in findings:
    f["is_new"] = False

existing_ids = {f["id"] for f in findings}

added = 0
for raw in new_findings_raw:
    fid = make_id(raw["url"], raw["title"])
    if fid in existing_ids:
        continue
    findings.append({
        "id": fid,
        "detected_at": DETECTED_AT,
        "source_name": raw["source_name"],
        "url": raw["url"],
        "region": raw["region"],
        "tier": raw["tier"],
        "lang": raw["lang"],
        "title": raw["title"],
        "summary_en": raw["summary_en"],
        "relevance": raw["relevance"],
        "is_new": True,
    })
    existing_ids.add(fid)
    added += 1

# Keep newest ~500
findings = findings[-500:]

with open(FINDINGS_PATH, "w", encoding="utf-8") as f:
    json.dump(findings, f, ensure_ascii=False, indent=2)

print(f"Added {added} new findings. Total: {len(findings)}.")
