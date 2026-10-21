"""Proposed item taxonomy: AttachDef Type/SubType + record path -> category or exclusion motive.

Phase 2A proposal written into exploration.md; normalize.py reuses it once validated.
Rules are checked in order: test objects, excluded paths/names, (Type, SubType), Type, category paths, Type exclusions.
"""

import re

TEST_PATTERN = re.compile(r"(?:^|[_/.])(?:test|debug|dev|template|placeholder|prototype|protoype|dummy|deprecated)(?:[_/.\d]|$)")

EXCLUDED_PATHS = (
    ("entities/scitem/human/lt/prop/", "decor"),
    ("entities/scitem/characters/creature/", "apparence du personnage"),
    ("character/", "apparence du personnage"),
)
EXCLUDED_NAME_PREFIXES = (("entityspawner_", "objets non portables"),)  # spawners of an item, not the item
# Ship-gun internals (Barrel/FiringMechanism/PowerArray/Ventilation) share the WeaponAttachment type with FPS
# attachments; only their folder tells them apart. ships/weapons/ also holds real WeaponGun items, hence type + path.
EXCLUDED_TYPE_PATHS = (
    ("WeaponAttachment", "entities/scitem/ships/weapons/", "objets non portables"),
    ("WeaponAttachment", "entities/scitem/weapons/weapon_mounted/", "objets non portables"),
)
CATEGORY_PATHS = (
    ("entities/scitem/carryables/", "objets transportables"),
    ("entities/decorations/", "decoration"),
)

# (Type, SubType or None) -> (category, subcategory)
CATEGORY_RULES = {
    ("WeaponPersonal", "Gadget"): ("outils", "multi-outils et gadgets d'arme"),
    ("WeaponPersonal", "Small"): ("armes", "pistolets"),
    ("WeaponPersonal", "Medium"): ("armes", "fusils"),
    ("WeaponPersonal", "Large"): ("armes", "armes lourdes"),
    ("WeaponPersonal", "Knife"): ("armes", "couteaux"),
    ("WeaponPersonal", "Grenade"): ("armes", "grenades"),
    ("WeaponPersonal", None): ("armes", "autres armes"),
    ("Grenade", None): ("armes", "grenades"),
    ("WeaponAttachment", "Magazine"): ("munitions", "chargeurs"),
    ("WeaponAttachment", None): ("armes", "accessoires d'arme"),
    ("Light", "Weapon"): ("armes", "accessoires d'arme"),
    ("AmmoBox", None): ("munitions", "boites de munitions"),
    ("Missile", None): ("munitions", "missiles et torpilles"),
    ("Bomb", None): ("munitions", "bombes"),
    ("SpaceMine", None): ("munitions", "mines"),
    ("Char_Armor_Helmet", None): ("armures", "casques"),
    ("Char_Armor_Torso", None): ("armures", "torse"),
    ("Char_Armor_Arms", None): ("armures", "bras"),
    ("Char_Armor_Legs", None): ("armures", "jambes"),
    ("Char_Armor_Backpack", None): ("armures", "sacs a dos"),
    ("Char_Armor_Undersuit", None): ("armures", "sous-combinaisons"),
    ("Suit", "ThrusterPack"): ("armures", "jetpacks"),
    ("Char_Clothing_Torso_0", None): ("vetements", "hauts"),
    ("Char_Clothing_Torso_1", None): ("vetements", "vestes"),
    ("Char_Clothing_Torso_2", None): ("vetements", "manteaux"),
    ("Char_Clothing_Legs", None): ("vetements", "bas"),
    ("Char_Clothing_Feet", None): ("vetements", "chaussures"),
    ("Char_Clothing_Hands", None): ("vetements", "gants"),
    ("Char_Clothing_Hat", None): ("vetements", "chapeaux"),
    ("Char_Clothing_Backpack", None): ("vetements", "sacs"),
    ("Char_Accessory_Eyes", None): ("vetements", "lunettes"),
    ("Char_Accessory_Head", None): ("vetements", "accessoires de tete"),
    ("Char_Flair", None): ("vetements", "accessoires"),
    ("Visor", None): ("vetements", "visieres"),
    ("PowerPlant", None): ("composants de vaisseau", "generateurs"),
    ("Shield", None): ("composants de vaisseau", "boucliers"),
    ("Cooler", None): ("composants de vaisseau", "refroidisseurs"),
    ("QuantumDrive", None): ("composants de vaisseau", "moteurs quantiques"),
    ("JumpDrive", None): ("composants de vaisseau", "moteurs de saut"),
    ("Radar", None): ("composants de vaisseau", "radars"),
    ("LifeSupportGenerator", None): ("composants de vaisseau", "support vital"),
    ("WeaponGun", None): ("composants de vaisseau", "armes de vaisseau"),
    ("MissileLauncher", None): ("composants de vaisseau", "lance-missiles"),
    ("GroundVehicleMissileLauncher", None): ("composants de vaisseau", "lance-missiles"),
    ("BombLauncher", None): ("composants de vaisseau", "lance-bombes"),
    ("WeaponDefensive", None): ("composants de vaisseau", "contre-mesures"),
    ("QuantumInterdictionGenerator", None): ("composants de vaisseau", "interdiction quantique"),
    ("EMP", None): ("composants de vaisseau", "IEM"),
    ("TractorBeam", None): ("composants de vaisseau", "rayons tracteurs"),
    ("TowingBeam", None): ("composants de vaisseau", "rayons tracteurs"),
    ("SalvageHead", None): ("composants de vaisseau", "recuperation"),
    ("SalvageModifier", None): ("composants de vaisseau", "recuperation"),
    ("WeaponMining", None): ("minage", "lasers de minage"),
    ("MiningModifier", None): ("minage", "modules de minage"),
    ("Misc", "Harvestable"): ("minage", "recolte"),
    ("Scanner", None): ("outils", "scanners"),
    ("FPS_Radar", None): ("outils", "radars portatifs"),
    ("Gadget", None): ("gadgets", "gadgets"),
    ("FPS_Deployable", None): ("gadgets", "deployables"),
    ("RemovableChip", None): ("gadgets", "puces"),
    ("MobiGlas", None): ("gadgets", "mobiGlas"),
    ("FPS_Consumable", "Hacking"): ("gadgets", "piratage"),
    ("FPS_Consumable", None): ("consommables", "medical"),
    ("Food", None): ("consommables", "nourriture"),
    ("Drink", None): ("consommables", "boissons"),
    ("Bottle", None): ("decoration", "bouteilles"),
    ("Flair_Surface", None): ("decoration", "surfaces"),
    ("Flair_Floor", None): ("decoration", "sol"),
    ("Flair_Wall", None): ("decoration", "murs"),
    ("Flair_Cockpit", None): ("decoration", "cockpit"),
    ("InventoryContainer", None): ("objets transportables", "conteneurs"),
    ("Container", None): ("objets transportables", "conteneurs"),
    ("Currency", None): ("objets transportables", "monnaie"),
}

EXCLUDED_TYPES = {
    "vaisseaux": {"NOITEM_Vehicle:Vehicle_Spaceship", "Paints"},
    "vehicules": {"NOITEM_Vehicle:Vehicle_GroundVehicle", "NOITEM_Vehicle:Vehicle_PowerSuit"},
    "portes": {"Door", "ControlPanel", "Decal", "Sensor", "Button", "Lightgroup", "StatusScreen", "DoorController", "Elevator"},
    "decor": {
        "Display",
        "ShopDisplay",
        "Usable",
        "Room",
        "Hangar",
        "PersistentHab",
        "Thumbnail",
        "Audio",
        "Debris",
        "Light",
        "Seat",
        "SeatAccess",
        "SeatDashboard",
        "Cloth",
        "AttachedPart",
        "AirTrafficController",
        "AIModule",
        "UNDEFINED",
        "Misc",
    },
    "apparence du personnage": {
        "Player",
        "NOITEM_Player",
        "Char_Body",
        "Char_Head",
        "Char_Head_Hair",
        "Char_Head_Beard",
        "Char_Head_Eyes",
        "Char_Head_Eyebrow",
        "Char_Head_Eyelash",
        "Char_Head_Piercings",
        "Char_Head_Stubble",
        "Char_Hair_Color",
        "Char_Hair_Prop",
        "Char_Skin_Color",
        "Char_Lens",
    },
    "objets non portables": {
        "ManneuverThruster",
        "MainThruster",
        "FuelTank",
        "FuelIntake",
        "QuantumFuelTank",
        "ExternalFuelTank",
        "FlightController",
        "ShieldController",
        "WeaponController",
        "MissileController",
        "SalvageController",
        "MiningController",
        "WheeledController",
        "CommsController",
        "EnergyController",
        "CapacitorAssignmentController",
        "CoolerController",
        "FuelController",
        "LightController",
        "TargetSelector",
        "SelfDestruct",
        "Transponder",
        "LifeSupportVent",
        "GravityGenerator",
        "Relay",
        "CargoGrid",
        "TurretBase",
        "Turret",
        "UtilityTurret",
        "WeaponMount",
        "ToolArm",
        "SalvageInternalStorage",
        "SalvageFillerStation",
        "SalvageFieldEmitter",
        "SalvageFieldSupporter",
        "DockingCollar",
        "DockingAnimator",
        "LandingGear",
        "LandingSystem",
        "Module",
        "Armor",
        "RemoteConnection",
        "Crafter",
        "Battery",
        "Cargo",
        "Misc:Mineable",  # MineableRock_* world rocks; the carryable ores live under carryables/
    },
}
EXCLUSION_BY_TYPE = {member: motive for motive, members in EXCLUDED_TYPES.items() for member in members}


def early_exclusion(item):
    """Motive that wins over any category rule (test objects, decor paths, spawners), or None."""
    if TEST_PATTERN.search(item["path"]):
        return "objets de test"
    by_path = next((motive for prefix, motive in EXCLUDED_PATHS if item["path"].startswith(prefix)), None)
    by_name = next((motive for prefix, motive in EXCLUDED_NAME_PREFIXES if item["name"].lower().startswith(prefix)), None)
    by_type_path = next(
        (motive for kind, prefix, motive in EXCLUDED_TYPE_PATHS if item.get("type") == kind and item["path"].startswith(prefix)),
        None,
    )
    return by_path or by_name or by_type_path


def classify(item):
    """Return ("category", category, subcategory), ("excluded", motive, None) or ("unmapped", None, None)."""
    path, kind, subtype = item["path"], item.get("type"), item.get("subtype")
    motive = early_exclusion(item)
    if motive:
        return "excluded", motive, None
    rule = CATEGORY_RULES.get((kind, subtype)) or CATEGORY_RULES.get((kind, None))
    if rule:
        return "category", *rule
    for prefix, category in CATEGORY_PATHS:
        if path.startswith(prefix):
            return "category", category, (kind or "UNDEFINED").lower()
    motive = EXCLUSION_BY_TYPE.get(f"{kind}:{subtype}") or EXCLUSION_BY_TYPE.get(kind)
    if motive:
        return "excluded", motive, None
    return "unmapped", None, None
