import unittest

from app.authoring.exam_graph_spec import (
    FAST_SCENE_SCHEMA_ID,
    GraphSpecError,
    graph_scene,
    normalize_graph_object,
    normalize_figure_objects,
)
from app.authoring.figures import FiveELocalProvider
from app.authoring.providers import DEVELOPER_INSTRUCTIONS


def sample_graph():
    return {
        "type": "graph", "box": [-30, -20, 60, 40],
        "xRange": [0, 10], "yRange": [0, 5],
        "xLabel": "시간(s)", "yLabel": "속력(m/s)",
        "markers": [{"x": 2, "y": 3, "label": "A"}],
        "series": [{"kind": "line", "points": [[0, 0], [10, 5]],
                    "label": "v", "labelRole": "quantity"}],
    }


class ExamGraphSpecTest(unittest.TestCase):
    def test_line_coordinate_aliases_are_canonicalized_for_5e(self):
        result = normalize_figure_objects([{
            "type": "line", "x1": -30, "y1": -10,
            "x2": 30, "y2": 15, "lineMode": "middleArrow",
        }])
        self.assertEqual(result[0]["p1"], {"x": -30.0, "y": -10.0})
        self.assertEqual(result[0]["p2"], {"x": 30.0, "y": 15.0})
        self.assertNotIn("x1", result[0])
        self.assertNotIn("x2", result[0])

    def test_line_endpoints_must_be_distinct_finite_points(self):
        with self.assertRaises(GraphSpecError):
            normalize_figure_objects([{
                "type": "line", "p1": [0, 0], "p2": {"x": 0, "y": 0},
            }])

    def test_kice_defaults_and_label_roles_are_canonical(self):
        graph = normalize_graph_object(sample_graph())
        self.assertFalse(graph["grid"])
        self.assertEqual(graph["axisStrokeWidth"], 0.3)
        self.assertEqual(graph["seriesStrokeWidth"], 0.55)
        self.assertEqual(graph["markers"][0]["labelRole"], "label")
        self.assertEqual(graph["series"][0]["labelRole"], "quantity")

    def test_invalid_range_or_label_role_is_rejected_before_5e(self):
        graph = sample_graph()
        graph["xRange"] = [4, 4]
        with self.assertRaises(GraphSpecError):
            normalize_graph_object(graph)
        graph = sample_graph()
        graph["markers"][0]["labelRole"] = "italic"
        with self.assertRaises(GraphSpecError):
            normalize_graph_object(graph)

    def test_include_text_false_strips_nested_graph_labels(self):
        graph = normalize_figure_objects([sample_graph()], include_text=False)[0]
        self.assertNotIn("xLabel", graph)
        self.assertNotIn("yLabel", graph)
        self.assertNotIn("label", graph["markers"][0])
        self.assertNotIn("label", graph["series"][0])
        self.assertFalse(graph["axisLabels"])

    def test_graph_scene_uses_5e_fast_scene_contract(self):
        graph = normalize_graph_object(sample_graph())
        scene = graph_scene({"w": 90, "h": 60}, [graph])
        self.assertEqual(scene["schema"], FAST_SCENE_SCHEMA_ID)
        self.assertEqual(scene["mode"], "complete")
        self.assertEqual(scene["elements"], [graph])

    def test_provider_routes_graphs_to_add_scene_and_native_objects_to_add_objects(self):
        class Client:
            def __init__(self):
                self.calls = []

            def call(self, name, arguments=None):
                self.calls.append((name, arguments))

        client = Client()
        provider = FiveELocalProvider(root=".")
        provider._add_plan_objects(
            client,
            [{"type": "line", "p1": {"x": 0, "y": 0}, "p2": {"x": 1, "y": 1}},
             normalize_graph_object(sample_graph())],
            {"w": 90, "h": 60},
            path="figure.pending.json",
        )
        self.assertEqual([name for name, _ in client.calls], ["add_scene"])
        self.assertEqual(client.calls[0][1]["scene"]["schema"], FAST_SCENE_SCHEMA_ID)
        self.assertEqual(client.calls[0][1]["objects"][0]["type"], "line")
        self.assertTrue(client.calls[0][1]["strict"])

    def test_authoring_prompt_contains_graph_typography_contract(self):
        self.assertIn('labelRole="quantity"', DEVELOPER_INSTRUCTIONS)
        self.assertIn("axisStrokeWidth", DEVELOPER_INSTRUCTIONS)
        self.assertIn("원시 line/text 조합이 아니라", DEVELOPER_INSTRUCTIONS)


if __name__ == "__main__":
    unittest.main()
