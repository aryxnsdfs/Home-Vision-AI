import os
import math
from typing import List, Dict, Any
from PIL import Image, ImageDraw, ImageFont

# Define a color palette based on room types
ROOM_COLORS = {
    "living_room": (224, 242, 254),    # Light Blue
    "kitchen": (254, 240, 138),        # Light Yellow
    "dining": (254, 215, 170),         # Light Orange
    "bedroom": (216, 180, 254),        # Light Purple
    "master_bedroom": (192, 132, 252), # Medium Purple
    "bathroom": (167, 243, 208),       # Light Emerald
    "corridor": (243, 244, 246),       # Light Gray
    "staircase": (209, 213, 219),      # Gray
    "balcony": (191, 219, 254),        # Soft Blue
    "unknown": (255, 255, 255)
}

class BlueprintRenderer:
    @staticmethod
    def render_blueprint(nodes: List[Any], plot_width: float, plot_length: float, filename: str = "blueprint.png") -> str:
        """
        Renders a 2D colored floor plan from RoomNodes using PIL.
        Saves it to the public directory and returns the URL path.
        """
        # Scale for rendering (e.g. 1 foot = 20 pixels)
        scale = 20
        img_w = int(plot_width * scale) + 100
        img_h = int(plot_length * scale) + 100
        
        # Create a white canvas
        img = Image.new("RGB", (img_w, img_h), "white")
        draw = ImageDraw.Draw(img)
        
        # Try to load a font, otherwise use default
        try:
            font = ImageFont.truetype("arial.ttf", 14)
            large_font = ImageFont.truetype("arial.ttf", 18)
        except IOError:
            font = ImageFont.load_default()
            large_font = ImageFont.load_default()
            
        offset_x = 50
        offset_z = 50
        
        # Draw plot boundary
        draw.rectangle(
            [offset_x, offset_z, offset_x + plot_width * scale, offset_z + plot_length * scale],
            outline="black", width=2
        )
        
        # Draw rooms
        for node in nodes:
            room_type = node.type.lower()
            color = ROOM_COLORS.get(room_type)
            if not color:
                for key, c in ROOM_COLORS.items():
                    if key in room_type:
                        color = c
                        break
            if not color:
                color = ROOM_COLORS["unknown"]
                
            rx = offset_x + node.rect.x * scale
            rz = offset_z + node.rect.z * scale
            rw = node.rect.width * scale
            rl = node.rect.length * scale
            
            # Draw room box
            draw.rectangle([rx, rz, rx + rw, rz + rl], fill=color, outline="black", width=2)
            
            # Draw room label
            label = node.name.replace(" ", "\n")
            # Calculate approx text size to center it
            # Text centering fallback if getsize is deprecated
            try:
                tw, th = draw.textsize(label, font=font)
            except AttributeError:
                # Pillow 10+
                bbox = draw.textbbox((0, 0), label, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                
            tx = rx + (rw - tw) / 2
            tz = rz + (rl - th) / 2
            draw.text((tx, tz), label, fill="black", font=font)
            
            # Draw doors
            if hasattr(node, 'doors'):
                for door in node.doors:
                    dx = offset_x + door.x * scale
                    dz = offset_z + door.z * scale
                    dw = door.width * scale
                    
                    if door.wall_orientation in ("north", "south"):
                        draw.rectangle([dx, dz - 3, dx + dw, dz + 3], fill="brown")
                    else:
                        draw.rectangle([dx - 3, dz, dx + 3, dz + dw], fill="brown")
                        
            # Draw windows
            if hasattr(node, 'windows'):
                for win in node.windows:
                    wx = offset_x + win.x * scale
                    wz = offset_z + win.z * scale
                    ww = win.width * scale
                    
                    if win.wall_orientation in ("north", "south"):
                        draw.rectangle([wx, wz - 2, wx + ww, wz + 2], fill="lightblue")
                    else:
                        draw.rectangle([wx - 2, wz, wx + 2, wz + ww], fill="lightblue")
        
        # Save image
        out_dir = os.path.join(os.path.dirname(__file__), "public", "blueprints")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, filename)
        img.save(out_path)
        
        return f"/blueprints/{filename}"
