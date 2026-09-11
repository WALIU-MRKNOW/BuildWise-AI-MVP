from __future__ import annotations

import base64
from io import BytesIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from PIL import Image as PILImage

from bot.config import Settings
from bot.handlers.sketch_report_handlers import render_sketch_report
from bot.keyboards import (
    elevation_result_keyboard,
    more_views_keyboard,
    sketch_analysis_keyboard,
    view_result_keyboard,
)
from bot.services.sketch_elevation_service import (
    SketchElevationError,
    SketchElevationService,
)
from bot.sketch_elevation import (
    SketchElevationSpec,
    elevation_generation_prompt,
    view_generation_prompt,
)


def settings() -> Settings:
    return Settings(
        _env_file=None,
        TELEGRAM_BOT_TOKEN="123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijk",
        OPENAI_API_KEY="sk-test-placeholder",
        SUPABASE_URL="https://example.supabase.co",
        SUPABASE_SECRET_KEY="sb_secret_test_placeholder",
    )


def tiny_png() -> bytes:
    buffer = BytesIO()
    PILImage.new("RGB", (40, 30), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class SketchElevationSpecTests(unittest.TestCase):
    def test_spec_separates_confirmed_facts_assumptions_and_uncertainty(self) -> None:
        spec = SketchElevationSpec.from_mapping(
            {
                "drawing_type": "floor plan",
                "building_type": "bungalow",
                "storeys": 1,
                "front_side": "entrance-facing south side",
                "entrance": "center",
                "footprint": "rectangular with front porch",
                "visible_openings": ["two front windows"],
                "confirmed_features": ["front porch"],
                "dimensions": ["overall width 12 m"],
                "roof": "not shown",
                "left_side_features": ["one labelled window"],
                "right_side_features": ["service door"],
                "rear_features": ["rear kitchen window"],
                "roof_features": [],
                "assumptions": ["roof pitch 30 degrees"],
                "uncertainties": ["window height not shown"],
            }
        )
        self.assertEqual(spec.building_type, "bungalow")
        self.assertEqual(spec.storeys, 1)
        self.assertEqual(spec.footprint, "rectangular with front porch")
        self.assertIn("one labelled window", spec.left_side_features)
        self.assertIn("roof pitch 30 degrees", spec.assumptions)
        self.assertIn("window height not shown", spec.uncertainties)

    def test_front_generation_prompt_forbids_random_facade_changes(self) -> None:
        spec = SketchElevationSpec(
            entrance="center-left",
            visible_openings=["three windows"],
            assumptions=["wall height 3.0 m"],
        )
        prompt = elevation_generation_prompt(spec, "Visible front entrance and windows")
        self.assertIn("front elevation", prompt.lower())
        self.assertIn("Do not invent extra storeys", prompt)
        self.assertIn("PRIMARY visual reference", prompt)
        self.assertIn("one fixed building", prompt)

    def test_side_and_roof_prompts_keep_same_building(self) -> None:
        spec = SketchElevationSpec(
            footprint="12 m by 9 m rectangle",
            roof="hip roof",
            left_side_features=["two left-side windows"],
            assumptions=["wall height 3.0 m"],
        )
        left = view_generation_prompt(spec, "Plan", view="left")
        top = view_generation_prompt(spec, "Plan", view="top")
        self.assertIn("LEFT SIDE", left)
        self.assertIn("same roof/storey assumptions", left)
        self.assertIn("TOP / ROOF VIEW", top)
        self.assertIn("directly from above", top)
        self.assertIn("same building", top.lower())


class SketchElevationKeyboardTests(unittest.TestCase):
    def test_sketch_image_has_only_elevation_and_pdf(self) -> None:
        key = "00000000-0000-0000-0000-000000000001"
        markup = sketch_analysis_keyboard(key, can_generate_elevation=True)
        labels = [button.text for row in markup.inline_keyboard for button in row]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertEqual(labels, ["🏠 Elevation", "📄 PDF"])
        self.assertIn(f"elevgen:{key}", callbacks)
        self.assertIn(f"sketchpdf:{key}", callbacks)

    def test_front_result_has_more_views_and_pdf_only(self) -> None:
        key = "00000000-0000-0000-0000-000000000001"
        markup = elevation_result_keyboard(key)
        labels = [button.text for row in markup.inline_keyboard for button in row]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertEqual(labels, ["🧱 More Views", "📄 Add to PDF"])
        self.assertIn(f"elevmenu:{key}", callbacks)

    def test_more_views_submenu_contains_left_right_rear_top_and_back(self) -> None:
        key = "00000000-0000-0000-0000-000000000001"
        markup = more_views_keyboard(key)
        labels = [button.text for row in markup.inline_keyboard for button in row]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertEqual(
            labels,
            ["⬅️ Left Side", "➡️ Right Side", "🔙 Rear", "🔝 Top / Roof", "↩️ Back"],
        )
        self.assertIn(f"elevview:left:{key}", callbacks)
        self.assertIn(f"elevview:right:{key}", callbacks)
        self.assertIn(f"elevview:rear:{key}", callbacks)
        self.assertIn(f"elevview:top:{key}", callbacks)

    def test_generated_secondary_view_has_pdf_and_regenerate_only(self) -> None:
        key = "00000000-0000-0000-0000-000000000001"
        markup = view_result_keyboard(key, "left")
        labels = [button.text for row in markup.inline_keyboard for button in row]
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        self.assertEqual(labels, ["📄 Add to PDF", "🔁 Regenerate"])
        self.assertIn(f"elevregen:left:{key}", callbacks)


class SketchElevationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_reference_sketch_is_structured_then_sent_to_image_edit(self) -> None:
        service = SketchElevationService(settings())
        structure = {
            "drawing_type": "floor plan",
            "building_type": "bungalow",
            "storeys": 1,
            "front_side": "entrance-facing side",
            "entrance": "center",
            "footprint": "rectangle",
            "visible_openings": ["two windows"],
            "confirmed_features": ["porch"],
            "dimensions": ["12 m width"],
            "roof": "not shown",
            "left_side_features": [],
            "right_side_features": [],
            "rear_features": [],
            "roof_features": [],
            "assumptions": ["roof pitch 30 degrees"],
            "uncertainties": [],
        }
        generated = b"generated-elevation-bytes"
        responses = SimpleNamespace(
            create=AsyncMock(
                return_value=SimpleNamespace(output_text=json.dumps(structure))
            )
        )
        images = SimpleNamespace(
            edit=AsyncMock(
                return_value=SimpleNamespace(
                    data=[
                        SimpleNamespace(
                            b64_json=base64.b64encode(generated).decode("ascii")
                        )
                    ]
                )
            )
        )
        service._client = SimpleNamespace(responses=responses, images=images)

        result, spec = await service.generate(
            image_bytes=b"source-sketch",
            image_mime_type="image/jpeg",
            analysis="Layout: bungalow with a center entrance.",
        )

        self.assertEqual(result, generated)
        self.assertEqual(spec.building_type, "bungalow")
        responses.create.assert_awaited_once()
        images.edit.assert_awaited_once()
        kwargs = images.edit.await_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-image-2.5-sunburst")
        self.assertEqual(kwargs["size"], "1536x1024")
        self.assertEqual(kwargs["quality"], "medium")
        self.assertEqual(kwargs["image"][1], b"source-sketch")
        self.assertEqual(kwargs["image"][2], "image/jpeg")

    async def test_secondary_view_reuses_existing_spec_without_restructuring(self) -> None:
        service = SketchElevationService(settings())
        spec = SketchElevationSpec(
            building_type="bungalow",
            footprint="rectangle",
            roof="hip roof",
            left_side_features=["two windows"],
            assumptions=["wall height 3.0 m"],
        )
        generated = b"left-view"
        responses = SimpleNamespace(create=AsyncMock())
        images = SimpleNamespace(
            edit=AsyncMock(
                return_value=SimpleNamespace(
                    data=[SimpleNamespace(b64_json=base64.b64encode(generated).decode("ascii"))]
                )
            )
        )
        service._client = SimpleNamespace(responses=responses, images=images)

        result, returned_spec = await service.generate(
            image_bytes=b"source-sketch",
            image_mime_type="image/png",
            analysis="Plan",
            view="left",
            spec=spec,
        )

        self.assertEqual(result, generated)
        self.assertIs(returned_spec, spec)
        responses.create.assert_not_awaited()
        prompt = images.edit.await_args.kwargs["prompt"]
        self.assertIn("LEFT SIDE", prompt)
        self.assertIn("same building", prompt.lower())

    async def test_unsupported_reference_format_is_rejected_before_api_call(self) -> None:
        service = SketchElevationService(settings())
        with self.assertRaises(SketchElevationError):
            await service.generate(
                image_bytes=b"gif",
                image_mime_type="image/gif",
                analysis="Sketch",
            )

    async def test_unsupported_view_is_rejected(self) -> None:
        service = SketchElevationService(settings())
        with self.assertRaises(SketchElevationError):
            await service.generate(
                image_bytes=b"source-sketch",
                image_mime_type="image/png",
                analysis="Sketch",
                view="underside",
                spec=SketchElevationSpec(),
            )


class SketchElevationPdfTests(unittest.TestCase):
    def test_elevation_and_assumptions_embed_in_sketch_pdf(self) -> None:
        pdf = render_sketch_report(
            "Layout\nSimple bungalow.\n\nImportant Review\nVerify dimensions.",
            elevation_bytes=tiny_png(),
            elevation_assumptions=["wall height 3.0 m", "roof pitch 30 degrees"],
        )
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 1200)

    def test_multiple_generated_views_embed_in_same_pdf(self) -> None:
        pdf = render_sketch_report(
            "Layout\nSimple bungalow.",
            generated_views={
                "front": tiny_png(),
                "left": tiny_png(),
                "rear": tiny_png(),
                "top": tiny_png(),
            },
            elevation_assumptions=["wall height 3.0 m", "hip roof assumed"],
        )
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 1800)


if __name__ == "__main__":
    unittest.main()
