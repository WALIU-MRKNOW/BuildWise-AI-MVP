import unittest

from bot.domain import Mode
from bot.formatting import format_response


class StudentLearningHeaderTests(unittest.TestCase):
    def test_topic_teaching_sections_use_topic_header(self) -> None:
        answer = (
            "📚 Meaning\nA foundation transfers building loads to the ground.\n\n"
            "🔑 Key Points\n- It spreads loads.\n- It connects the structure to the soil.\n\n"
            "🏗️ Construction Example\nA pad foundation may support a column.\n\n"
            "🎯 Exam / Study Tip\nRemember the load-transfer function."
        )
        self.assertIn("EXPLAINING TOPIC", format_response(Mode.STUDY, answer))


if __name__ == "__main__":
    unittest.main()
