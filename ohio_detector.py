import re
from typing import Optional

KNOWN_OHIO_CITIES = [
    "Akron", "Athens", "Avon", "Avon Lake", "Bay Village", "Beavercreek", 
    "Bowling Green", "Broadview Heights", "Canton", "Centerville", "Chillicothe", 
    "Cincinnati", "Cleveland", "Cleveland Heights", "Columbus", "Cuyahoga Falls", 
    "Dayton", "Dublin", "Elyria", "Euclid", "Fairborn", "Fairfield", "Findlay", 
    "Forest Park", "Gahanna", "Grove City", "Hamilton", "Harrison", "Hilliard", 
    "Huber Heights", "Kettering", "Lakewood", "Lancaster", "Lebanon", "Lima", 
    "Lorain", "Mansfield", "Marietta", "Marion", "Maumee", "Miamisburg", 
    "Middletown", "Monroe", "Moraine", "Newark", "North Ridgeville", 
    "North Olmsted", "Olmsted Falls", "Oregon", "Oxford", "Parma", 
    "Parma Heights", "Pickerington", "Port Clinton", "Portsmouth", "Reynoldsburg", 
    "Riverside", "Rocky River", "Sandusky", "Seven Hills", "Sharonville", 
    "Springdale", "Springfield", "Steubenville", "Sylvania", "Toledo", "Trotwood", 
    "Upper Arlington", "Westlake", "Westerville", "Worthington", "Xenia", 
    "Youngstown", "Zanesville"
]

KNOWN_OHIO_COUNTIES = [
    "Adams", "Allen", "Ashland", "Ashtabula", "Athens", "Auglaize", 
    "Belmont", "Brown", "Butler", "Carroll", "Champaign", "Clark", 
    "Clermont", "Clinton", "Columbiana", "Coshocton", "Crawford", "Cuyahoga", 
    "Darke", "Defiance", "Delaware", "Erie", "Fairfield", "Fayette", 
    "Franklin", "Fulton", "Gallia", "Geauga", "Greene", "Guernsey", 
    "Hamilton", "Hancock", "Hardin", "Harrison", "Henry", "Highland", 
    "Hocking", "Holmes", "Huron", "Jackson", "Jefferson", "Knox", 
    "Lake", "Lawrence", "Licking", "Logan", "Lorain", "Lucas", 
    "Madison", "Mahoning", "Marion", "Medina", "Meigs", "Mercer", 
    "Miami", "Monroe", "Montgomery", "Morgan", "Morrow", "Muskingum", 
    "Noble", "Ottawa", "Paulding", "Perry", "Pickaway", "Pike", 
    "Portage", "Preble", "Putnam", "Richland", "Ross", "Sandusky", 
    "Scioto", "Seneca", "Shelby", "Stark", "Summit", "Trumbull", 
    "Tuscarawas", "Union", "Van Wert", "Vinton", "Warren", "Washington", 
    "Wayne", "Williams", "Wood", "Wyandot"
]

KNOWN_STATE_AGENCIES = [
    "Ohio State Highway Patrol",
    "Ohio State Police",
    "OSHP",
    "Ohio BCI",
    "Bureau of Criminal Investigation",
    "Ohio Department of Public Safety",
    "ODPS"
]

BODY_CAM_KEYWORDS = [
    "body cam", "bodycam", "body camera", "bwc", "body worn camera",
    "patrol cam", "officer cam", "police cam", "on-body camera"
]

DASH_CAM_KEYWORDS = [
    "dash cam", "dashcam", "dashboard camera", "in-car camera", "cruiser cam",
    "patrol car camera", "dash camera", "windshield cam", "in car camera"
]

GENERIC_POLICE_KEYWORDS = [
    "traffic stop", "arrest footage", "police footage", "law enforcement footage",
    "officer footage", "police video", "deputy footage", "sheriff footage"
]

def find_matched_ohio_cities(text: str) -> list[str]:
    text_lower = text.lower()
    matched = []
    for city in KNOWN_OHIO_CITIES:
        if city.lower() in text_lower:
            matched.append(city)
    return matched

def is_ohio_location(text: str) -> bool:
    text_lower = text.lower()
    # Check cities
    for city in KNOWN_OHIO_CITIES:
        if city.lower() in text_lower:
            return True
    # Check counties with "County"
    for county in KNOWN_OHIO_COUNTIES:
        if county.lower() in text_lower and "county" in text_lower:
            return True
        if f"{county.lower()} county" in text_lower:
            return True
    # Direct Ohio references
    if re.search(r'\boh\b', text_lower):
        return True
    if "ohio" in text_lower:
        return True
    return False

def is_ohio_police_entity(text: str) -> bool:
    text_lower = text.lower()
    for agency in KNOWN_STATE_AGENCIES:
        if agency.lower() in text_lower:
            return True
    # Check for "Ohio" + police/sheriff/department patterns
    if "ohio" in text_lower and any(k in text_lower for k in ["police", "sheriff", "department", "pd", "sheriff's office"]):
        return True
    # Check for city + police/sheriff for known Ohio cities
    for city in KNOWN_OHIO_CITIES:
        pattern = f"{city.lower()} (police|sheriff|department|pd|office)"
        if re.search(pattern, text_lower):
            return True
    # Check for Ohio counties with sheriff
    for county in KNOWN_OHIO_COUNTIES:
        pattern = f"{county.lower()} (county )?(sheriff|police|department)"
        if re.search(pattern, text_lower):
            return True
    return False

def is_body_cam_or_dash_cam(text: str) -> bool:
    text_lower = text.lower()
    # Body cam
    for kw in BODY_CAM_KEYWORDS:
        if kw in text_lower:
            return True
    # Dash cam
    for kw in DASH_CAM_KEYWORDS:
        if kw in text_lower:
            return True
    # Generic police footage (more lenient)
    for kw in GENERIC_POLICE_KEYWORDS:
        if kw in text_lower:
            return True
    return False

def calculate_ohio_confidence(video: dict) -> tuple[float, float, str, list[str]]:
    """Returns confidence scores, match reason, and matched city names."""
    title = video.get("title", "")
    description = video.get("description", "")
    channel = video.get("channel", "") or video.get("uploader", "")
    
    combined = f"{title} {description} {channel}"
    
    ohio_score = 0.0
    reasons = []
    matched_cities = find_matched_ohio_cities(combined)
    
    # Strong Ohio signals
    if is_ohio_police_entity(combined):
        ohio_score += 70
        reasons.append("Matches known Ohio police entity")
    
    # City match gets a +30 bonus with city-specific reason
    if matched_cities:
        ohio_score += 30
        reasons.append(f"Ohio city identified: {', '.join(matched_cities)}")
    elif is_ohio_location(combined):
        ohio_score += 30
        reasons.append("Ohio location identified")
    
    # Weak but supportive
    if "ohio" in combined.lower() or re.search(r'\boh\b', combined.lower()):
        ohio_score += 15
        reasons.append("Ohio reference found")
    
    ohio_score = min(ohio_score, 100.0)
    
    # Cap at 100 and also evaluate body cam/dash cam presence
    cam_score = 0.0
    cam_reasons = []
    
    if is_body_cam_or_dash_cam(combined):
        cam_score = 100.0
        cam_reasons.append("Body cam or dash cam content detected")
    
    return ohio_score, cam_score, "; ".join(reasons), matched_cities
