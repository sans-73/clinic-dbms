"""p4_fake.py - FAKE Indian-style demo data (no real people). Deterministic via random.Random(seed)."""
from __future__ import annotations

import random
from datetime import timedelta

MALE = ["Ramesh", "Suresh", "Mahesh", "Rajesh", "Anil", "Sunil", "Vijay", "Amit", "Rohit", "Sanjay", "Deepak", "Manoj",
        "Arun", "Pankaj", "Nitin", "Vikas", "Rahul", "Ajay", "Mohammad", "Imran", "Faisal", "Gurpreet", "Prakash",
        "Dinesh", "Kamal", "Naveen", "Yogesh", "Harish", "Lokesh", "Tarun"]
FEMALE = ["Sunita", "Priya", "Anita", "Kavita", "Meena", "Rekha", "Pooja", "Neha", "Sneha", "Anjali", "Shweta", "Ritu",
          "Savita", "Geeta", "Seema", "Shabnam", "Farzana", "Kiran", "Lakshmi", "Radha", "Usha", "Asha", "Mamta", "Nisha"]
SURNAMES = ["Sharma", "Verma", "Gupta", "Singh", "Yadav", "Patel", "Mishra", "Pandey", "Tiwari", "Joshi", "Chouhan",
            "Rathore", "Jain", "Agrawal", "Khan", "Ansari", "Qureshi", "Kushwaha", "Dubey", "Thakur", "Meena", "Soni",
            "Malviya", "Saxena", "Shukla", "Bhatt", "Nair", "Iyer", "Reddy", "Das"]
STEMS = ["Para", "Amoxi", "Azithro", "Cefi", "Panto", "Ome", "Rani", "Metro", "Cipro", "Levo", "Ceti", "Mont", "Dolo",
         "Ibu", "Diclo", "Nime", "Ator", "Rosu", "Metfor", "Gliben", "Amlo", "Telmi", "Losar", "Ond", "Dom", "Vita",
         "Calci", "Ferro", "Zinc", "Bcom", "Predni", "Dexa", "Salbu", "Bude", "Fluco", "Itra", "Tera", "Doxy", "Clox", "Rifa"]
SUFFIX = ["cetamol", "xil", "mycin", "xime", "prazole", "zole", "tidine", "nidazole", "floxacin", "cetirizine", "lukast",
          "fen", "fenac", "sulide", "vastatin", "min", "pine", "sartan", "setron", "peridone", "min-D", "um", "lone", "sone"]
BRAND_TAIL = ["", " Plus", " DS", " Forte", " SR", " XL", " Kid", " LS", " Duo", " MR"]


def phone(rng: random.Random) -> str:
    return rng.choice("6789") + "".join(rng.choice("0123456789") for _ in range(9))


def person(rng: random.Random):
    g = rng.choice(["male", "female"])
    first = rng.choice(MALE if g == "male" else FEMALE)
    return first, rng.choice(SURNAMES), g


def dob(rng: random.Random, today, lo=1, hi=85):
    return today - timedelta(days=rng.randint(lo * 365, hi * 365))


def medicine_names(rng: random.Random, n: int) -> list[tuple[str, str]]:
    """(brand, generic) pairs; brands are invented, unique."""
    seen, out = set(), []
    guard = 0
    while len(out) < n and guard < n * 50:
        guard += 1
        stem, suf = rng.choice(STEMS), rng.choice(SUFFIX)
        generic = (stem + suf).capitalize()
        brand = generic[:rng.randint(4, 7)] + rng.choice(["ex", "ol", "ix", "an", "in", "al"]) + rng.choice(BRAND_TAIL)
        brand = brand.strip().capitalize() + f" {rng.choice([5, 10, 20, 25, 40, 50, 100, 125, 250, 500, 650])}"
        if brand not in seen:
            seen.add(brand)
            out.append((brand, generic))
    return out
