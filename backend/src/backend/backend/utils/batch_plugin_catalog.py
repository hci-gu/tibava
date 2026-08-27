import copy

from backend.models import PluginRunResult, Timeline
from backend.plugin_manager import PluginManager


SHOT_OUTPUT = "shotdetection.timelines.shots"


def parameter(field, name, value=None, text=None, **kwargs):
    result = {
        "field": field,
        "name": name,
        "value": value,
        "text": text or name,
    }
    result.update(kwargs)
    return result


def simple_batch(parameter_strategy="shared"):
    return {
        "supported": True,
        "unsupported_reason": "",
        "allow_duplicate": False,
        "file_inputs": "unsupported",
        "parameter_strategies": {"default": parameter_strategy},
        "outputs": {},
    }


def unsupported(reason):
    return {
        "supported": False,
        "unsupported_reason": reason,
        "allow_duplicate": False,
        "file_inputs": "unsupported",
        "parameter_strategies": {},
        "outputs": {},
    }


BATCH_PLUGIN_CATALOG = [
    {
        "id": 1,
        "name": "Audio",
        "children": [
            {
                "name": "Audio RMS",
                "description": "Compute audio RMS over time.",
                "icon": "mdi-waveform",
                "plugin": "audio_rms",
                "parameters": [parameter("text_field", "timeline", "Audio RMS")],
                "optional_parameters": [
                    parameter("slider", "sr", 8000, "Sample rate", min=1000, max=24000, step=1000)
                ],
                "batch": simple_batch(),
            },
            {
                "name": "Audio frequency",
                "description": "Compute audio frequency features.",
                "icon": "mdi-waveform",
                "plugin": "audio_freq",
                "parameters": [parameter("text_field", "timeline", "Audio Frequency")],
                "optional_parameters": [
                    parameter("slider", "sr", 8000, "Sample rate", min=1000, max=24000, step=1000),
                    parameter("slider", "n_fft", 256, "FFT size", min=64, max=512, step=64),
                ],
                "batch": simple_batch(),
            },
            {
                "name": "Audio waveform",
                "description": "Compute audio waveform amplitude.",
                "icon": "mdi-waveform",
                "plugin": "audio_amp",
                "parameters": [parameter("text_field", "timeline", "Audio Waveform")],
                "optional_parameters": [
                    parameter("slider", "sr", 8000, "Sample rate", min=1000, max=24000, step=1000)
                ],
                "batch": simple_batch(),
            },
            {
                "name": "Whisper",
                "description": "Transcribe speech.",
                "icon": "mdi-waveform",
                "plugin": "whisper",
                "parameters": [parameter("text_field", "timeline", "Transcript")],
                "optional_parameters": [],
                "batch": simple_batch(),
            },
            {
                "name": "Whisper X",
                "description": "Transcribe speech with alignment.",
                "icon": "mdi-waveform",
                "plugin": "whisper_x",
                "parameters": [parameter("text_field", "timeline", "Transcript")],
                "optional_parameters": [
                    parameter("text_field", "language_code", "none", "Language code")
                ],
                "batch": simple_batch(),
            },
            {
                "name": "Audio emotion",
                "description": "Classify audio emotion.",
                "icon": "mdi-waveform",
                "plugin": "audio_emotion",
                "parameters": [parameter("text_field", "timeline", "Audio Emotion")],
                "optional_parameters": [],
                "batch": simple_batch(),
            },
            {
                "name": "Audio gender",
                "description": "Classify speaker gender.",
                "icon": "mdi-waveform",
                "plugin": "audio_gender",
                "parameters": [parameter("text_field", "timeline", "Audio Gender")],
                "optional_parameters": [],
                "batch": simple_batch(),
            },
            {
                "name": "Audio classification",
                "description": "Classify audio segments.",
                "icon": "mdi-waveform",
                "plugin": "audio_classification",
                "parameters": [
                    parameter("text_field", "timeline", "Audio Classification"),
                    parameter(
                        "select_options",
                        "segment_type",
                        "Shot",
                        "Segment type",
                        items=["Shot", "Speaker"],
                    ),
                ],
                "optional_parameters": [],
                "batch": simple_batch(),
            },
        ],
    },
    {
        "id": 2,
        "name": "Face",
        "children": [
            {
                "name": "Face clustering",
                "description": "Cluster detected faces.",
                "icon": "mdi-ungroup",
                "plugin": "face_clustering",
                "parameters": [
                    parameter("slider", "cluster_threshold", 0.5, "Cluster threshold", min=0.3, max=0.7, step=0.01),
                    parameter("slider", "max_cluster", 50, "Max clusters", min=1, max=100, step=1),
                    parameter("slider", "max_samples_per_cluster", 20, "Max samples per cluster", min=1, max=100, step=1),
                    parameter("slider", "min_face_height", 0.1, "Min face height", min=0, max=1.0, step=0.05),
                ],
                "optional_parameters": [
                    parameter("select_options", "clustering_method", "DBScan", "Clustering method", items=["Agglomerative", "DBScan"]),
                    parameter("slider", "fps", 2, "FPS", min=1, max=10, step=1),
                ],
                "batch": simple_batch(),
            },
            {
                "name": "Face identification",
                "description": "Identify faces from query images.",
                "icon": "mdi-account-search",
                "plugin": "insightface_identification",
                "parameters": [
                    parameter("text_field", "timeline", "Face Identification"),
                    parameter("image_input", "query_images", None, "Query images"),
                ],
                "optional_parameters": [parameter("slider", "fps", 2, "FPS", min=1, max=10, step=1)],
                "batch": {
                    **simple_batch(),
                    "file_inputs": "shared",
                    "parameter_strategies": {"query_images": ["shared_file"]},
                },
            },
            {
                "name": "Face emotion",
                "description": "Classify face emotion from shot segments.",
                "icon": "mdi-emoticon-happy-outline",
                "plugin": "deepface_emotion",
                "parameters": [
                    parameter("text_field", "timeline", "Face Emotion"),
                    parameter("select_timeline", "shot_timeline_id", None, "Shot timeline"),
                ],
                "optional_parameters": [
                    parameter("slider", "fps", 2, "FPS", min=1, max=10, step=1),
                    parameter("slider", "min_facesize", 48, "Min face size", min=24, max=256, step=8),
                ],
                "batch": {
                    **simple_batch(),
                    "parameter_strategies": {
                        "shot_timeline_id": ["previous_step_output", "timeline_by_name"]
                    },
                },
            },
            {
                "name": "Active speaker detection",
                "description": "Detect active speakers from shot segments.",
                "icon": "mdi-waveform",
                "plugin": "active_speaker_detection",
                "parameters": [
                    parameter("text_field", "timeline", "Active Speaker"),
                    parameter("select_timeline", "shot_timeline_id", None, "Shot timeline"),
                ],
                "optional_parameters": [parameter("slider", "fps", 25, "FPS", min=1, max=30, step=1)],
                "batch": {
                    **simple_batch(),
                    "parameter_strategies": {
                        "shot_timeline_id": ["previous_step_output", "timeline_by_name"]
                    },
                },
            },
        ],
    },
    {
        "id": 3,
        "name": "Visual",
        "children": [
            {
                "name": "Color analysis",
                "description": "Analyze dominant colors.",
                "icon": "mdi-palette",
                "plugin": "color_analysis",
                "parameters": [
                    parameter("text_field", "timeline", "Colors"),
                    parameter("slider", "k", 1, "Colors", min=1, max=8, step=1),
                ],
                "optional_parameters": [
                    parameter("slider", "fps", 1, "FPS", min=1, max=24, step=1),
                    parameter("slider", "max_resolution", 48, "Max resolution", min=16, max=256, step=8),
                    parameter("slider", "max_iter", 10, "Max iterations", min=1, max=100, step=1),
                    parameter("select_options", "timeline_visualization", 0, "Visualization", items=[0, 1]),
                ],
                "batch": simple_batch(),
            },
            {
                "name": "Color brightness analysis",
                "description": "Analyze brightness over time.",
                "icon": "mdi-brightness-6",
                "plugin": "color_brightness_analysis",
                "parameters": [parameter("text_field", "timeline", "Brightness")],
                "optional_parameters": [
                    parameter("slider", "fps", 1, "FPS", min=1, max=24, step=1),
                    parameter("checkbox", "normalize", False, "Normalize"),
                ],
                "batch": simple_batch(),
            },
            {
                "name": "CLIP",
                "description": "Score video frames against a text query.",
                "icon": "mdi-eye",
                "plugin": "clip",
                "parameters": [
                    parameter("text_field", "timeline", "CLIP"),
                    parameter("text_field", "search_term", "", "Search term"),
                ],
                "optional_parameters": [parameter("slider", "fps", 1, "FPS", min=1, max=10, step=1)],
                "batch": simple_batch(),
            },
            {
                "name": "CLIP ontology",
                "description": "Classify frames against an ontology.",
                "icon": "mdi-file-tree",
                "plugin": "clip_ontology",
                "parameters": [
                    parameter("text_field", "timeline", "CLIP Ontology"),
                    parameter("select_timeline", "shot_timeline_id", None, "Shot timeline"),
                    parameter("csv_input", "concept_csv", None, "Ontology CSV"),
                ],
                "optional_parameters": [parameter("slider", "fps", 1, "FPS", min=1, max=10, step=1)],
                "batch": {
                    **simple_batch(),
                    "file_inputs": "shared",
                    "parameter_strategies": {
                        "shot_timeline_id": ["previous_step_output", "timeline_by_name"],
                        "concept_csv": ["shared_file"],
                    },
                },
            },
            {
                "name": "X-CLIP",
                "description": "Score video clips against a text query.",
                "icon": "mdi-eye",
                "plugin": "x_clip",
                "parameters": [
                    parameter("text_field", "timeline", "X-CLIP"),
                    parameter("text_field", "search_term", "", "Search term"),
                ],
                "optional_parameters": [parameter("slider", "fps", 1, "FPS", min=1, max=10, step=1)],
                "batch": simple_batch(),
            },
            {
                "name": "Places classification",
                "description": "Classify places from shot segments.",
                "icon": "mdi-map-marker",
                "plugin": "places_classification",
                "parameters": [
                    parameter("text_field", "timeline", "Places"),
                    parameter("select_timeline", "shot_timeline_id", None, "Shot timeline"),
                ],
                "optional_parameters": [parameter("slider", "fps", 2, "FPS", min=1, max=10, step=1)],
                "batch": {
                    **simple_batch(),
                    "parameter_strategies": {
                        "shot_timeline_id": ["previous_step_output", "timeline_by_name"]
                    },
                },
            },
            {
                "name": "Place clustering",
                "description": "Cluster places from shot segments.",
                "icon": "mdi-map-marker-multiple",
                "plugin": "place_clustering",
                "parameters": [parameter("select_timeline", "shot_timeline_id", None, "Shot timeline")],
                "optional_parameters": [
                    parameter("slider", "fps", 2, "FPS", min=1, max=10, step=1),
                    parameter("slider", "max_cluster", 50, "Max clusters", min=1, max=100, step=1),
                    parameter("select_options", "clustering_method", "DBScan", "Clustering method", items=["Agglomerative", "DBScan"]),
                ],
                "batch": {
                    **simple_batch(),
                    "parameter_strategies": {
                        "shot_timeline_id": ["previous_step_output", "timeline_by_name"]
                    },
                },
            },
            {
                "name": "BLIP VQA",
                "description": "Ask a visual question over shot segments.",
                "icon": "mdi-eye",
                "plugin": "blip_vqa",
                "parameters": [
                    parameter("text_field", "timeline", "BLIP VQA"),
                    parameter("select_timeline", "shot_timeline_id", None, "Shot timeline"),
                    parameter("text_field", "query_term", "", "Query term"),
                ],
                "optional_parameters": [],
                "batch": {
                    **simple_batch(),
                    "parameter_strategies": {
                        "shot_timeline_id": ["previous_step_output", "timeline_by_name"]
                    },
                },
            },
            {
                "name": "OCR",
                "description": "Detect text in video frames.",
                "icon": "mdi-text-shadow",
                "plugin": "ocr_video_detector_onnx",
                "parameters": [
                    parameter("text_field", "timeline", "OCR"),
                    parameter("text_field", "search_term", "", "Search term"),
                ],
                "optional_parameters": [parameter("slider", "fps", 2, "FPS", min=1, max=10, step=1)],
                "batch": simple_batch(),
            },
        ],
    },
    {
        "id": 5,
        "name": "Shot",
        "children": [
            {
                "name": "Shot detection",
                "description": "Detect shots.",
                "icon": "mdi-arrow-expand-horizontal",
                "plugin": "shotdetection",
                "parameters": [parameter("text_field", "timeline", "Shots")],
                "optional_parameters": [parameter("slider", "fps", 2, "FPS", min=1, max=10, step=1)],
                "batch": {
                    **simple_batch(),
                    "outputs": {"timelines": {"shots": "shot_timeline"}},
                },
            },
            {
                "name": "Shot density",
                "description": "Compute shot density from shot segments.",
                "icon": "mdi-sine-wave",
                "plugin": "shot_density",
                "parameters": [
                    parameter("text_field", "timeline", "Shot Density"),
                    parameter("select_timeline", "shot_timeline_id", None, "Shot timeline"),
                ],
                "optional_parameters": [
                    parameter("slider", "bandwidth", 10, "Bandwidth", min=1, max=60, step=1),
                    parameter("slider", "fps", 10, "FPS", min=1, max=10, step=1),
                ],
                "batch": {
                    **simple_batch(),
                    "parameter_strategies": {
                        "shot_timeline_id": ["previous_step_output", "timeline_by_name"]
                    },
                },
            },
            {
                "name": "Shot type classification",
                "description": "Classify camera setting from shots.",
                "icon": "mdi-video-switch",
                "plugin": "shot_type_classification",
                "parameters": [
                    parameter("text_field", "timeline", "Camera Setting"),
                    parameter("select_timeline", "shot_timeline_id", None, "Shot timeline"),
                ],
                "optional_parameters": [parameter("slider", "fps", 2, "FPS", min=1, max=10, step=1)],
                "batch": {
                    **simple_batch(),
                    "parameter_strategies": {
                        "shot_timeline_id": ["previous_step_output", "timeline_by_name"]
                    },
                },
            },
            {
                "name": "Shot scalar annotation",
                "description": "Annotate shots from scalar values.",
                "icon": "mdi-label-outline",
                "plugin": "shot_scalar_annotation",
                "parameters": [
                    parameter("text_field", "timeline", "Shot Scalar"),
                    parameter("select_timeline", "shot_timeline_id", None, "Shot timeline"),
                    parameter("select_scalar_timeline", "scalar_timeline_id", None, "Scalar timeline"),
                ],
                "optional_parameters": [],
                "batch": {
                    **simple_batch(),
                    "parameter_strategies": {
                        "shot_timeline_id": ["previous_step_output", "timeline_by_name"],
                        "scalar_timeline_id": ["scalar_timeline_by_name"],
                    },
                },
            },
            {
                "name": "Shot angle",
                "description": "Classify shot angles.",
                "icon": "mdi-image-multiple",
                "plugin": "shot_angle",
                "parameters": [
                    parameter("text_field", "timeline", "Shot Angle"),
                    parameter("select_timeline", "shot_timeline_id", None, "Shot timeline"),
                ],
                "optional_parameters": [parameter("slider", "fps", 2, "FPS", min=1, max=10, step=1)],
                "batch": {
                    **simple_batch(),
                    "parameter_strategies": {
                        "shot_timeline_id": ["previous_step_output", "timeline_by_name"]
                    },
                },
            },
            {
                "name": "Shot level",
                "description": "Classify shot levels.",
                "icon": "mdi-image-multiple",
                "plugin": "shot_level",
                "parameters": [
                    parameter("text_field", "timeline", "Shot Level"),
                    parameter("select_timeline", "shot_timeline_id", None, "Shot timeline"),
                ],
                "optional_parameters": [parameter("slider", "fps", 2, "FPS", min=1, max=10, step=1)],
                "batch": {
                    **simple_batch(),
                    "parameter_strategies": {
                        "shot_timeline_id": ["previous_step_output", "timeline_by_name"]
                    },
                },
            },
            {
                "name": "Shot scale and movement",
                "description": "Classify shot scale and movement.",
                "icon": "mdi-image-multiple",
                "plugin": "shot_scale_and_movement",
                "parameters": [
                    parameter("text_field", "timeline_scale", "Shot Scale"),
                    parameter("text_field", "timeline_movement", "Shot Movement"),
                    parameter("select_timeline", "shot_timeline_id", None, "Shot timeline"),
                ],
                "optional_parameters": [parameter("slider", "fps", 25, "FPS", min=5, max=60, step=5)],
                "batch": {
                    **simple_batch(),
                    "parameter_strategies": {
                        "shot_timeline_id": ["previous_step_output", "timeline_by_name"]
                    },
                },
            },
            {
                "name": "Thumbnail",
                "description": "Generate thumbnails.",
                "icon": "mdi-image-multiple",
                "plugin": "thumbnail",
                "parameters": [],
                "optional_parameters": [],
                "batch": simple_batch(),
            },
        ],
    },
    {
        "id": 6,
        "name": "Aggregation",
        "children": [
            {
                "name": "Aggregate scalar",
                "description": "Aggregate scalar timelines.",
                "icon": "mdi-sigma",
                "plugin": "aggregate_scalar",
                "parameters": [
                    parameter("text_field", "timeline", "Aggregate Scalar"),
                    parameter("select_scalar_timelines", "timeline_ids", None, "Scalar timelines"),
                    parameter("buttongroup", "aggregation", 0, "Aggregation", buttons=["OR", "AND", "Mean", "Product"]),
                ],
                "optional_parameters": [],
                "batch": {
                    **simple_batch(),
                    "parameter_strategies": {"timeline_ids": ["scalar_timelines_by_name"]},
                },
            },
            {
                "name": "Invert scalar",
                "description": "Invert a scalar timeline.",
                "icon": "mdi-numeric-negative-1",
                "plugin": "invert_scalar",
                "parameters": [
                    parameter("text_field", "timeline", "Inverted Scalar"),
                    parameter("select_scalar_timeline", "scalar_timeline_id", None, "Scalar timeline"),
                ],
                "optional_parameters": [],
                "batch": {
                    **simple_batch(),
                    "parameter_strategies": {"scalar_timeline_id": ["scalar_timeline_by_name"]},
                },
            },
        ],
    },
    {
        "id": 7,
        "name": "Text",
        "children": [
            {
                "name": "Named entity recognition",
                "description": "Extract named entities from text.",
                "icon": "mdi-translate",
                "plugin": "text_ner",
                "parameters": [parameter("text_field", "timeline", "Named Entities")],
                "optional_parameters": [],
                "batch": simple_batch(),
            },
            {
                "name": "Part-of-speech tagging",
                "description": "Tag text parts of speech.",
                "icon": "mdi-translate",
                "plugin": "text_pos",
                "parameters": [
                    parameter("text_field", "timeline", "Part Of Speech"),
                    parameter("text_field", "language_code", "de", "Language code"),
                ],
                "optional_parameters": [],
                "batch": simple_batch(),
            },
            {
                "name": "Text sentiment",
                "description": "Classify text sentiment.",
                "icon": "mdi-translate",
                "plugin": "text_sentiment",
                "parameters": [
                    parameter("text_field", "timeline", "Sentiment"),
                    parameter(
                        "select_options",
                        "model_type",
                        "Multilingual",
                        "Model",
                        items=["German-News", "German-General", "Multilingual"],
                    ),
                ],
                "optional_parameters": [],
                "batch": simple_batch(),
            },
        ],
    },
]


def list_batch_plugin_catalog():
    plugin_manager = PluginManager()
    groups = copy.deepcopy(BATCH_PLUGIN_CATALOG)
    for group in groups:
        for plugin in group["children"]:
            batch = plugin["batch"]
            if plugin["plugin"] not in plugin_manager:
                batch["supported"] = False
                batch["unsupported_reason"] = "Plugin is not registered in the backend."
    return groups


def flatten_batch_plugin_catalog():
    entries = {}
    for group in list_batch_plugin_catalog():
        for plugin in group["children"]:
            entry = copy.deepcopy(plugin)
            entry["group"] = group["name"]
            entries[entry["plugin"]] = entry
    return entries


def batch_metadata_for_plugin(plugin):
    return flatten_batch_plugin_catalog().get(plugin)


def timeline_by_name(video, name, scalar=False):
    timelines = Timeline.objects.filter(video=video, name=name)
    if scalar:
        timelines = timelines.filter(plugin_run_result__type=PluginRunResult.TYPE_SCALAR)
    return timelines.order_by("order", "id").first()
