"""vision 路由决策单元测试。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.vision import VisionRouter


class _FakeClient:
    def __init__(self, modality="auto", detected=None):
        self.info = _Info(modality, detected)


class _Info:
    def __init__(self, modality, detected):
        self.modality = modality
        self.detected_modality = detected


class _FakeRegistry:
    def __init__(self, main=None, vision=None):
        self._main = main
        self._vision = vision

    def get(self, name):
        if name == "main":
            return self._main
        if name == "vision":
            return self._vision
        return None


class _FakeSettings:
    def __init__(self, policy, vision_enabled=True):
        self._policy = policy
        self._vision_enabled = vision_enabled

    def endpoints(self):
        return {"vision": {"enabled": self._vision_enabled}}

    def vision_routing(self):
        return {"policy": self._policy}


class TestVisionRouter(unittest.TestCase):
    def test_prefer_vision_model_default(self):
        settings = _FakeSettings("prefer_vision_model")
        registry = _FakeRegistry(
            main=_FakeClient("multimodal"), vision=_FakeClient()
        )
        router = VisionRouter(settings, registry)
        name, _ = router.select_model()
        self.assertEqual(name, "vision")

    def test_vision_model_only_uses_vision(self):
        settings = _FakeSettings("vision_model_only")
        registry = _FakeRegistry(main=_FakeClient("multimodal"), vision=_FakeClient())
        router = VisionRouter(settings, registry)
        name, _ = router.select_model()
        self.assertEqual(name, "vision")

    def test_vision_model_only_no_vision_raises(self):
        settings = _FakeSettings("vision_model_only", vision_enabled=False)
        registry = _FakeRegistry(main=_FakeClient("multimodal"), vision=None)
        router = VisionRouter(settings, registry)
        with self.assertRaises(ValueError):
            router.select_model()

    def test_prefer_main_if_multimodal_reuses_main(self):
        settings = _FakeSettings("prefer_main_if_multimodal")
        registry = _FakeRegistry(
            main=_FakeClient("multimodal"), vision=_FakeClient()
        )
        router = VisionRouter(settings, registry)
        name, _ = router.select_model()
        self.assertEqual(name, "main")

    def test_prefer_main_if_multimodal_text_main_falls_back(self):
        settings = _FakeSettings("prefer_main_if_multimodal")
        registry = _FakeRegistry(main=_FakeClient("text"), vision=_FakeClient())
        router = VisionRouter(settings, registry)
        name, _ = router.select_model()
        self.assertEqual(name, "vision")

    def test_prefer_vision_model_no_vision_uses_multimodal_main(self):
        settings = _FakeSettings("prefer_vision_model", vision_enabled=False)
        registry = _FakeRegistry(main=_FakeClient("multimodal"), vision=None)
        router = VisionRouter(settings, registry)
        name, _ = router.select_model()
        self.assertEqual(name, "main")

    def test_auto_modality_uses_probe(self):
        settings = _FakeSettings("prefer_main_if_multimodal", vision_enabled=False)
        registry = _FakeRegistry(main=_FakeClient("auto", detected="multimodal"), vision=None)
        router = VisionRouter(settings, registry)
        name, _ = router.select_model()
        self.assertEqual(name, "main")

    def test_no_capability_raises(self):
        settings = _FakeSettings("prefer_vision_model", vision_enabled=False)
        registry = _FakeRegistry(main=_FakeClient("text"), vision=None)
        router = VisionRouter(settings, registry)
        with self.assertRaises(ValueError):
            router.select_model()

    def test_attach_image_to_messages(self):
        settings = _FakeSettings("prefer_vision_model")
        registry = _FakeRegistry(main=_FakeClient(), vision=_FakeClient())
        router = VisionRouter(settings, registry)
        messages = [{"role": "user", "content": "看图"}]
        router.attach_image_to_messages(
            messages,
            [{"type": "image", "base64": "AAAA", "mime_type": "image/png"}],
        )
        self.assertEqual(len(messages), 1)
        content = messages[0]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        self.assertIn("data:image/png;base64,AAAA", content[1]["image_url"]["url"])


if __name__ == "__main__":
    unittest.main()
