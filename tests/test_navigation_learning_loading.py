import unittest

from bot.domain import Mode
from bot.handlers.navigation_bot_handlers import (
    NavigationBotHandlers,
    _current_study_request_kind,
)
from bot.study_experience import StudyRequestKind


class NavigationLearningLoadingTests(unittest.TestCase):
    def test_calculation_loading_label(self) -> None:
        token = _current_study_request_kind.set(StudyRequestKind.CALCULATION)
        try:
            self.assertEqual(
                NavigationBotHandlers._loading_text(Mode.STUDY),
                "🧮 Solving calculations...",
            )
        finally:
            _current_study_request_kind.reset(token)

    def test_topic_loading_label(self) -> None:
        token = _current_study_request_kind.set(StudyRequestKind.TOPIC)
        try:
            self.assertEqual(
                NavigationBotHandlers._loading_text(Mode.STUDY),
                "📚 Explaining topic...",
            )
        finally:
            _current_study_request_kind.reset(token)


if __name__ == "__main__":
    unittest.main()
