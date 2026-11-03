"""Frame-aware transparent graphics for boxes, keypoints and cluster cards."""

import base64
from bisect import bisect_right
from collections import defaultdict
import io

from PIL import Image, ImageDraw, ImageFont


SKELETON = [(0, 1), (0, 2), (1, 3), (2, 4), (5, 6), (5, 7), (7, 9),
            (6, 8), (8, 10), (5, 11), (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16)]


def rotate_point(x, y, rotation):
    rotation = round(rotation) % 360
    if rotation == 90:
        return y, 1 - x
    if rotation == 180:
        return 1 - x, 1 - y
    if rotation == 270:
        return 1 - y, x
    return x, y


def display_box(box, rotation, width, height):
    x, y, w, h = box
    points = [rotate_point(px, py, rotation) for px, py in [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]]
    return (max(0, min(point[0] for point in points) * width),
            max(0, min(point[1] for point in points) * height),
            min(width - 1, max(point[0] for point in points) * width),
            min(height - 1, max(point[1] for point in points) * height))


def load_font(size):
    for name in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf"]:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default(size=size)


class OverlayScene:
    def __init__(self, snapshot, width, height, rotation=0, panel_height=0):
        self.width, self.height, self.rotation = width, height, rotation
        self.panel_height = panel_height
        self.font_size = max(12, round(min(width, height) * 0.026 * snapshot["font_scale"]))
        self.font = load_font(self.font_size)
        self.layers = []
        self.thumbnails = {}
        for layer in snapshot["layers"]:
            if "records" not in layer:
                continue
            groups = defaultdict(list)
            for index, record in enumerate(layer["records"]):
                key = record.get("track_id") or record.get("identity") or str(index)
                groups[key].append(record)
            tracks = []
            for records in groups.values():
                records.sort(key=lambda record: record["time"])
                if layer["mode"] == "cluster_cards":
                    merged = []
                    for record in records:
                        if merged and record["time"] <= merged[-1]["end"]:
                            merged[-1]["end"] = max(merged[-1]["end"], record["end"])
                        else:
                            merged.append(dict(record))
                    records = merged
                tracks.append(([record["time"] for record in records], records))
            self.layers.append((layer, tracks))
            for card in layer.get("cards", []):
                if card.get("thumbnail"):
                    with Image.open(io.BytesIO(base64.b64decode(card["thumbnail"]))) as image:
                        self.thumbnails[card["id"]] = image.convert("RGBA")

    def current_records(self, layer, tracks, timestamp):
        records = []
        for times, track in tracks:
            index = bisect_right(times, timestamp) - 1
            if index < 0:
                continue
            current = track[index]
            if timestamp >= current["end"]:
                continue
            current = dict(current)
            if layer.get("smooth") and index + 1 < len(track) and "bbox" in current:
                following = track[index + 1]
                gap = following["time"] - current["time"]
                if 0 < gap <= 0.75 and following["time"] <= current["end"] + 0.001:
                    ratio = max(0, min(1, (timestamp - current["time"]) / gap))
                    current["bbox"] = [a + (b - a) * ratio for a, b in zip(current["bbox"], following["bbox"])]
                    if len(current.get("keypoints", [])) == len(following.get("keypoints", [])):
                        current["keypoints"] = [[a[0] + (b[0] - a[0]) * ratio, a[1] + (b[1] - a[1]) * ratio, min(a[2], b[2])]
                                                for a, b in zip(current.get("keypoints", []), following.get("keypoints", []))]
            records.append(current)
        return records

    def text(self, draw, xy, text, color, max_width=None):
        text = " ".join(str(text).split())
        max_width = max_width or self.width - xy[0] - 8
        while text and draw.textlength(text, font=self.font) > max_width:
            text = text[:-2].rstrip("…") + "…" if len(text) > 2 else ""
        if text:
            bounds = draw.textbbox(xy, text, font=self.font)
            draw.rectangle((bounds[0] - 3, bounds[1] - 2, bounds[2] + 3, bounds[3] + 2), fill=(0, 0, 0, 185))
            draw.text(xy, text, font=self.font, fill=color)

    def frame(self, timestamp):
        image = Image.new("RGBA", (self.width, self.height))
        draw = ImageDraw.Draw(image)
        legends = []
        for layer, tracks in self.layers:
            records = self.current_records(layer, tracks, timestamp)
            active = {record.get("identity") for record in records}
            if layer["mode"] == "cluster_cards" or layer.get("show_legend"):
                legends.extend((card, card["id"] in active) for card in layer.get("cards", [])
                               if layer["mode"] != "cluster_cards" or card["id"] in active)
            for record in records:
                if "bbox" not in record:
                    continue
                color = record.get("color", layer["color"])
                if "speaker_score" in record:
                    color = "#69F0AE" if record["speaker_score"] >= 0 else "#B0BEC5"
                box = display_box(record["bbox"], self.rotation, self.width, self.height)
                if box[2] <= box[0] or box[3] <= box[1]:
                    continue
                if layer.get("show_boxes", True):
                    draw.rectangle(box, outline=color, width=max(2, self.font_size // 7))
                if layer.get("show_labels", True):
                    self.text(draw, (box[0] + 3, max(0, box[1] - self.font_size - 8)), record.get("label", layer["label"]), color)
                if layer.get("show_landmarks") or (layer["mode"] == "body_pose" and layer.get("show_skeleton", True)):
                    points = [(*rotate_point(point[0], point[1], self.rotation), point[2]) for point in record.get("keypoints", [])]
                    points = [(x * self.width, y * self.height, visible) for x, y, visible in points]
                    if layer["mode"] == "body_pose":
                        for a, b in SKELETON:
                            if max(a, b) < len(points) and points[a][2] > 0 and points[b][2] > 0:
                                draw.line((points[a][:2], points[b][:2]), fill=color, width=2)
                    for x, y, visible in points:
                        if visible > 0:
                            draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill=color)
        # Cards sit below metric rows and above spatial labels.
        card_width = max(80, min(150, self.width // 5))
        columns = max(1, (self.width - 16) // card_width)
        card_height = round(card_width * 0.85) + self.font_size
        for index, (card, active) in enumerate(legends):
            x = 8 + index % columns * card_width
            y = self.panel_height + 8 + index // columns * card_height
            draw.rectangle((x, y, x + card_width - 5, y + card_height - 5), fill=(0, 0, 0, 190),
                           outline=card["color"] if active else "#666666", width=3 if active else 1)
            thumb = self.thumbnails.get(card["id"])
            if thumb is not None:
                thumb = thumb.copy()
                thumb.thumbnail((card_width - 12, card_height - self.font_size - 16))
                image.alpha_composite(thumb, (x + (card_width - 5 - thumb.width) // 2, y + 5))
            self.text(draw, (x + 5, y + card_height - self.font_size - 9), card["label"], card["color"] if active else "#AAAAAA", card_width - 16)
        return image.tobytes()
