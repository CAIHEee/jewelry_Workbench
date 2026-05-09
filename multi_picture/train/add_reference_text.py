#!/usr/bin/env python3
"""Add centered Chinese reference text to images in this directory."""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


TEXT = "参考图"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def find_font() -> str | None:
    preferred = [
        "Noto Sans CJK SC",
        "Noto Sans CJK",
        "WenQuanYi Micro Hei",
        "WenQuanYi Zen Hei",
        "Microsoft YaHei",
        "SimHei",
    ]
    if not shutil.which("fc-match"):
        return None

    for name in preferred:
        try:
            result = subprocess.run(
                ["fc-match", "-f", "%{file}", name],
                check=True,
                text=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError:
            continue
        font_path = result.stdout.strip()
        if font_path and Path(font_path).exists():
            return font_path
    return None


def fit_font(draw: ImageDraw.ImageDraw, font_path: str | None, image_size: tuple[int, int]) -> ImageFont.ImageFont:
    width, height = image_size
    max_width = int(width * 0.62)
    max_height = int(height * 0.24)
    font_size = max(18, min(width, height) // 6)

    while font_size >= 18:
        font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
        bbox = draw.textbbox((0, 0), TEXT, font=font, stroke_width=max(2, font_size // 18))
        if bbox[2] - bbox[0] <= max_width and bbox[3] - bbox[1] <= max_height:
            return font
        font_size -= 2

    return ImageFont.truetype(font_path, 18) if font_path else ImageFont.load_default()


def add_text(image_path: Path, output_path: Path, font_path: str | None) -> None:
    with Image.open(image_path) as image:
        original_mode = image.mode
        canvas = image.convert("RGBA")
        overlay = Image.new("RGBA", canvas.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)
        font = fit_font(draw, font_path, canvas.size)

        stroke_width = max(2, min(canvas.size) // 170)
        bbox = draw.textbbox((0, 0), TEXT, font=font, stroke_width=stroke_width)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        position = (
            (canvas.width - text_width) / 2 - bbox[0],
            (canvas.height - text_height) / 2 - bbox[1],
        )

        draw.text(
            position,
            TEXT,
            font=font,
            fill=(255, 255, 255, 220),
            stroke_width=stroke_width,
            stroke_fill=(0, 0, 0, 170),
        )

        result = Image.alpha_composite(canvas, overlay)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if image_path.suffix.lower() in {".jpg", ".jpeg"}:
            result = result.convert("RGB")
            result.save(output_path, quality=95, subsampling=0)
        elif original_mode == "RGBA":
            result.save(output_path)
        else:
            result.convert(original_mode).save(output_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Add centered "参考图" text to images.')
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "watermarked",
        help="Directory for processed images. Defaults to ./watermarked.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite images in this directory instead of writing to --output-dir.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    font_path = find_font()
    image_paths = sorted(
        path for path in script_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )

    if not image_paths:
        raise SystemExit("No images found.")

    for image_path in image_paths:
        output_path = image_path if args.in_place else args.output_dir / image_path.name
        add_text(image_path, output_path, font_path)
        print(f"{image_path.name} -> {output_path.relative_to(script_dir)}")

    print(f"Done. Processed {len(image_paths)} image(s).")


if __name__ == "__main__":
    main()
