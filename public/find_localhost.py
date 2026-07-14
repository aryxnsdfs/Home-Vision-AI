from PIL import Image

def convert_to_square(input_path, output_path, size=(1024, 1024)):
    # Open image
    img = Image.open(input_path)

    # Get dimensions
    width, height = img.size

    # Determine square crop area (center crop)
    min_dim = min(width, height)
    left = (width - min_dim) // 2
    top = (height - min_dim) // 2
    right = left + min_dim
    bottom = top + min_dim

    # Crop to square
    img = img.crop((left, top, right, bottom))

    # Resize to 1024x1024
    img = img.resize(size, Image.Resampling.LANCZOS)

    # Save output
    img = img.convert("RGB")
    img.save(output_path, quality=95)


    print(f"Saved: {output_path}")

# Example usage
convert_to_square("logo.png", "output.jpg")