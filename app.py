# app.py
import streamlit as st
import folium
from streamlit_folium import folium_static
from folium.plugins import Fullscreen
import numpy as np
from shapely.validation import make_valid
from datetime import datetime
import plotly.express as px

st.set_page_config(page_title="Urban Congestion Pro", layout="wide")

from congestion_features import get_congestion_features


def _geom_to_coords(geom, inv):
    if geom.is_empty or not geom.is_valid:
        return []
    if geom.geom_type == 'Polygon':
        return [inv.transform(x, y) for x, y in geom.exterior.coords]
    if geom.geom_type == 'MultiPolygon' and len(geom.geoms) > 0:
        return [inv.transform(x, y) for x, y in geom.geoms[0].exterior.coords]
    if geom.geom_type == 'LineString':
        return [inv.transform(x, y) for x, y in geom.coords]
    return []


# ================================
# INPUTS
# ================================
col1, col2 = st.columns([1, 1])
with col1:
    coord = st.text_input("Coordinates (Lat, Lng)", "18.5204, 73.8567", help="e.g., 28.6139, 77.2090")
with col2:
    radius = st.slider("Analysis Radius (meters)", 100, 1500, 600, 50)

if not coord.strip():
    st.stop()

try:
    lat, lng = map(float, [x.strip() for x in coord.split(",")])
except:
    st.error("Invalid coordinates. Use format: latitude, longitude")
    st.stop()


# ================================
# FETCH OSM DATA
# ================================
with st.spinner("Fetching OpenStreetMap data..."):
    (metrics,
     buildings_with_type,
     road_details,
     buf,
     fwd,
     inv,
     water_polygons) = get_congestion_features(lat, lng, radius)


# ================================
# CALCULATIONS - NOW WITH WATER EXCLUDED (CORRECT METHOD)
# ================================
total_area_m2 = np.pi * radius ** 2
total_building_area = metrics["total_building_area_m2"]
total_road_area = metrics["total_road_area_m2"]
water_area = metrics["water_area_m2"]

# KEY FIX: Exclude permanent water from usable land
effective_land_area = max(total_area_m2 - water_area, 1)  # avoid divide-by-zero

# TRUE built-up ratio on actual land (not water)
used_area_ratio = (total_building_area + total_road_area) / effective_land_area

# True open space (excluding water)
true_open_space = effective_land_area - total_building_area - total_road_area

# Updated congestion score logic (Z-score method with proper clamping)
z_used = (used_area_ratio - 0.30) / 0.25
z_used = max(-2, min(2, z_used))                    # Clamp Z
raw_score = z_used * 5 + 5
congestion_score = max(0, min(10, round(raw_score, 1)))  # FINAL CLAMP TO 0–10
level = "HIGH" if congestion_score > 7 else "MEDIUM" if congestion_score > 4 else "LOW"


# ================================
# BUILDING COLORS & TOP 10
# ================================
type_areas = metrics["building_types_area"]
top_tags = [tag for tag, _ in sorted(type_areas.items(), key=lambda x: x[1], reverse=True)[:10]]

fixed_colors = {
    "building=house": "#d32f2f", "building=yes": "#1976d2", "building=apartments": "#388e3c",
    "building=residential": "#7b1fa2", "building=roof": "#f57c00", "building=garage": "#fbc02d",
    "building=commercial": "#c2185b", "building=retail": "#00897b", "building=industrial": "#455a64",
    "building=shed": "#5d4037"
}
palette = ["#8e44ad", "#3498db", "#e74c3c", "#2ecc71", "#f1c40f", "#e67e22", "#1abc9c", "#34495e", "#9b59b6", "#16a085"]
tag_to_color = {tag: fixed_colors.get(tag, palette[i % len(palette)]) for i, tag in enumerate(top_tags)}
tag_to_color["Other"] = "#95a5a6"


# ================================
# PIE CHARTS (still show water separately)
# ================================
fig_pie = px.pie(
    names=[t.replace("building=", "").title().replace("Yes", "Generic Building") for t in type_areas.keys()],
    values=list(type_areas.values()),
    title="Building Area by Type",
    color_discrete_sequence=[tag_to_color.get(t, "#95a5a6") for t in type_areas.keys()],
    hole=0.4
)

fig_land = px.pie(
    values=[total_building_area, total_road_area, water_area, true_open_space],
    names=["Buildings", "Roads", "Water Bodies", "True Open Space"],
    color_discrete_sequence=["#e67e22", "#c0392b", "#00bcd4", "#27ae60"],
    title="Land Use Breakdown",
    hole=0.4
)


# ================================
# MAP - CLEAN & BEAUTIFUL
# ================================
m = folium.Map(location=[lat, lng], zoom_start=16, tiles=None)

# Satellite + dark overlay
folium.TileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
                 attr='Esri', name='Satellite', show=True).add_to(m)
folium.TileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
                 attr='CartoDB', name='Contrast', overlay=True, opacity=0.35, subdomains='abcd').add_to(m)
folium.TileLayer('OpenStreetMap', name='OpenStreetMap', show=False).add_to(m)

Fullscreen(position="topleft").add_to(m)
folium.plugins.MousePosition(separator=' | ', prefix='Lat/Lng: ', num_digits=6).add_to(m)

folium.Circle([lat, lng], radius=radius, color="#00ff00", weight=10, fillOpacity=0.15, opacity=1,
              tooltip="Analysis Zone").add_to(m)

# Buildings
folium.GeoJson(
    {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [_geom_to_coords(geom, inv)]},
         "properties": {"name": btype.replace("building=", "").title().replace("Yes", "Generic Building"),
                        "area": f"{geom.area:,.0f}", "tag": btype}}
        for geom, btype in buildings_with_type
        if geom.is_valid and geom.area >= 20 and len(_geom_to_coords(geom, inv)) >= 3
    ]},
    style_function=lambda x: {
        "fillColor": tag_to_color.get(x["properties"]["tag"] if x["properties"]["tag"] in top_tags else "Other", "#95a5a6"),
        "color": "#2c3e50", "weight": 1.3, "fillOpacity": 0.85
    },
    tooltip=folium.GeoJsonTooltip(["name", "area"], aliases=["Type:", "Area (m²):"])
).add_to(m)

# Water Bodies
if water_polygons:
    water_geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": poly.type, "coordinates": [
                _geom_to_coords(poly, inv) if poly.type == "Polygon" else
                [_geom_to_coords(g, inv) for g in poly.geoms]
            ]},
            "properties": {"area": f"{poly.area:,.0f}"}
        } for poly in water_polygons if poly.is_valid and poly.area >= 200]
    }
    if water_geojson["features"]:
        folium.GeoJson(water_geojson,
                       style_function=lambda x: {"fillColor": "#00d4ff", "color": "#00ffff", "weight": 10, "fillOpacity": 0.8},
                       tooltip=folium.GeoJsonTooltip(["area"], aliases=["Water (m²):"])).add_to(m)

# Roads - Neon Glow (Single Clean Layer)
# === ROADS - Neon Glow + Smart Width Tooltip ===
# === ROADS - Soft & Premium Neon Glow (Lower Brightness) ===
roads_layer = folium.FeatureGroup(name="Roads (Neon Glow)", show=True)
roads_geojson = {"type": "FeatureCollection", "features": []}

for rd in road_details:
    line = rd["geom"]
    if line.is_empty or line.length < 5:
        continue

    length_str = f"Length: {line.length:.0f} m"
    width = rd["width"]
    source = rd["width_source"]

    width_str = ""
    if source in ("osm", "lanes"):
        if source == "osm":
            width_str = f"Width: {width:.1f} m (from OSM)".replace(".0 m", " m")
        else:
            width_str = f"Width: {width:.1f} m (estimated from lanes)".replace(".0 m", " m")

    tooltip_lines = [length_str]
    if width_str:
        tooltip_lines.append(width_str)
    tooltip_html = "<br>".join(tooltip_lines)

    coords = [inv.transform(x, y) for x, y in line.coords]
    roads_geojson["features"].append({
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"info": tooltip_html}
    })

# Soft & Elegant Neon (Lower Brightness, More Professional)
if roads_geojson["features"]:
    glow_styles = [
        {"color": "#ff4081", "weight": 32, "opacity": 0.25},   # Very soft outer glow
        {"color": "#ff79b0", "weight": 20, "opacity": 0.40},   # Subtle mid glow
        {"color": "#ffffff", "weight": 10, "opacity": 0.60},   # Soft white core
        {"color": "#ff1744", "weight": 6,  "opacity": 1.00}    # Bright red line (thin!)
    ]

    # Outer soft layers
    for style in glow_styles[:-1]:
        folium.GeoJson(
            roads_geojson,
            style_function=lambda x, s=style: s
        ).add_to(roads_layer)

    # Final sharp line with tooltip
    folium.GeoJson(
        roads_geojson,
        style_function=lambda x: glow_styles[-1],
        tooltip=folium.GeoJsonTooltip(
            fields=["info"],
            aliases=[""],
            style="""
                background: #0f0f0f;
                color: #00ff88;
                font-weight: bold;
                font-size: 14px;
                padding: 12px 16px;
                border-radius: 12px;
                border: 2px solid #00ff88;
                box-shadow: 0 0 20px rgba(0,255,136,0.5);
                font-family: 'Segoe UI', sans-serif;
                text-align: left;
                line-height: 1.7;
            """,
            sticky=True
        )
    ).add_to(roads_layer)

roads_layer.add_to(m)

folium.CircleMarker([lat, lng], radius=16, color="#ffd700", fillColor="#ff6b00", weight=5,
                    tooltip="Center").add_to(m)

folium.LayerControl(collapsed=False).add_to(m)


# ================================
# LEGEND
# ================================
legend_html = '''
<div style="position: fixed; bottom: 20px; left: 20px; width: 340px; background: rgba(255,255,255,0.98);
            border-radius: 16px; padding: 20px; box-shadow: 0 8px 40px rgba(0,0,0,0.45); z-index: 9999;
            font-family: 'Segoe UI', sans-serif; border: 4px solid #2c3e50;">
  <b style="font-size: 21px; color: #2c3e50;">Urban Congestion Pro • Legend</b>
  <hr style="margin: 12px 0; border-color: #ddd;">
  <div style="margin: 14px 0;"><i style="background:#ff1744; width:44px; height:14px; display:inline-block; margin-right:14px; 
     border-radius:8px; box-shadow: 0 0 20px #ff1744;"></i><b style="color:#c62828; font-size:17px;">Roads (Neon)</b></div>
  <div style="margin: 16px 0;"><i style="background:#00ffff; border:5px solid #00d4ff; width:34px; height:34px; display:inline-block; 
     margin:8px 14px 8px 0; border-radius:10px; box-shadow: 0 0 16px #00ffff;"></i><b style="color:#006064; font-size:17px;">Water Bodies</b></div>
  <hr style="margin: 14px 0; border-color: #ddd;">
'''
for tag in top_tags:
    clean = tag.replace("building=", "").title()
    if clean == "Yes": clean = "Generic Building"
    legend_html += f'<i style="background:{tag_to_color[tag]}; width:26px; height:26px; float:left; margin:8px 12px 8px 0; border-radius:8px; border:1px solid #444;"></i><span style="line-height:32px; font-size:15px;">{clean}</span><br>'
legend_html += '<i style="background:#95a5a6; width:26px; height:26px; float:left; margin:8px 12px 8px 0; border-radius:8px;"></i><span style="line-height:32px; font-size:15px;">Other Buildings</span></div>'
m.get_root().html.add_child(folium.Element(legend_html))


# ================================
# DISPLAY
# ================================
st.title("Urban Congestion Pro")
st.markdown("**Real-time urban density & land-use analysis using OpenStreetMap**")

col_map, col_report = st.columns([2.4, 1])

with col_map:
    st.subheader(f"Analysis Zone • {radius:,} m radius around {lat:.4f}°, {lng:.4f}°")
    folium_static(m, width=1200, height=750)
    c1, c2 = st.columns(2)
    with c1: st.plotly_chart(fig_land, use_container_width=True)
    with c2: st.plotly_chart(fig_pie, use_container_width=True)

with col_report:
    color = "#e74c3c" if level == "HIGH" else "#e67e22" if level == "MEDIUM" else "#27ae60"
    st.markdown(f"""
    <div style="background:{color}; color:white; padding:28px; border-radius:16px; text-align:center; margin-bottom:20px;">
        <h1 style="margin:0; font-size:56px;">{congestion_score}/10</h1>
        <h2 style="margin:8px 0 0 0; font-weight:600;">{level} Congestion</h2>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("### Land Use Summary")
    st.markdown(f"""
    <div style="background:#f8f9fa; padding:20px; border-radius:14px; line-height:2.2; font-size:15px;">
    • **Built-up Area (on land)**: {used_area_ratio:.1%}<br>
      &nbsp;&nbsp;↳ Buildings: {total_building_area:,.0f} m²<br>
      &nbsp;&nbsp;↳ Roads: {total_road_area:,.0f} m²<br><br>
    • **True Open Space**: {true_open_space:,.0f} m² ({true_open_space/effective_land_area:.1%})<br>
    • **Water Bodies (excluded)**: {water_area:,.0f} m² ({water_area/total_area_m2:.1%})
    </div>
    """, unsafe_allow_html=True)

    st.markdown("### Top 10 Building Types")
    for tag, area in sorted(type_areas.items(), key=lambda x: x[1], reverse=True)[:10]:
        clean = tag.replace("building=", "").title().replace("Yes", "Generic Building")
        pct = area / total_building_area * 100 if total_building_area > 0 else 0
        st.markdown(f"**{clean}** – {area:,.0f} m² ({pct:.1f}%)")

    st.caption(f"Analysis completed: {datetime.now().strftime('%B %d, %Y • %I:%M %p')} IST")