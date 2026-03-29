#!/usr/bin/env python3
"""
Generate glitter images for button backgrounds with source-specific colors.

Each source (Spotify1, Spotify2, Apple Music) gets a glitter image
matching its accent color.
"""

import random
from pathlib import Path
from PIL import Image, ImageDraw

# Define sources and their accent colors (hex)
SOURCES = {
    "spotify1": "#ff6b35",      # Orange
    "spotify2": "#1db954",      # Green
    "apple-music": "#0066cc",   # Blue
}

# Output directory
OUTPUT_DIR = Path(__file__).parent.parent / "frontend" / "public" / "icons" / "glitter"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

def hex_to_rgb(hex_color: str) -> tuple:
    """Convert hex color to RGB tuple."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

def generate_glitter(source_name: str, hex_color: str, width: int = 1200, height: int = 800):
    """Generate a glitter image with the given color."""
    rgb = hex_to_rgb(hex_color)
    
    # Create base image with dark background for contrast
    img = Image.new("RGB", (width, height), color=(15, 15, 15))  # Almost black
    draw = ImageDraw.Draw(img, "RGBA")
    
    # Generate random sparkles with varying sizes and opacity
    random.seed(42)  # Consistent results
    
    for _ in range(5000):  # Even more sparkles
        x = random.randint(0, width)
        y = random.randint(0, height)
        size = random.randint(4, 20)  # Bigger sparkles
        opacity = random.randint(180, 255)  # More visible
        
        # Create bright color variation (lighter versions)
        r = min(255, rgb[0] + random.randint(30, 80))
        g = min(255, rgb[1] + random.randint(30, 80))
        b = min(255, rgb[2] + random.randint(30, 80))
        
        # Draw main sparkle
        draw.ellipse(
            [(x - size//2, y - size//2), (x + size//2, y + size//2)],
            fill=(r, g, b, opacity)
        )
        
        # Add bright white highlight for extra shine
        if random.random() < 0.5:
            highlight_size = max(2, size // 3)
            draw.ellipse(
                [(x - highlight_size//2, y - highlight_size//2), (x + highlight_size//2, y + highlight_size//2)],
                fill=(255, 255, 255, int(opacity * 0.8))
            )
    
    # Save image
    output_path = OUTPUT_DIR / f"{source_name}.jpg"
    img.save(output_path, "JPEG", quality=95)
    print(f"✓ Generated {output_path.name} ({hex_color}) with high visibility")

def main():
    """Generate all glitter images."""
    print("Generating glitter images with source-specific colors...\n")
    
    for source_name, hex_color in SOURCES.items():
        generate_glitter(source_name, hex_color)
    
    print(f"\n✓ All glitter images generated in {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
