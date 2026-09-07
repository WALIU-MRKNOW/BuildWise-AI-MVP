import unittest

from telegram.ext import CallbackQueryHandler

from bot.handlers.student_learning_handlers import StudentLearningHandlers


class _FakeApplication:
    def __init__(self) -> None:
        self.handlers = []

    def add_handler(self, handler) -> None:
        self.handlers.append(handler)


class StudentPdfHandlerRegistrationTests(unittest.TestCase):
    def test_solution_pdf_callback_is_specific(self) -> None:
        app = _FakeApplication()
        StudentLearningHandlers().register(app)  # type: ignore[arg-type]
        self.assertEqual(len(app.handlers), 1)
        handler = app.handlers[0]
        self.assertIsInstance(handler, CallbackQueryHandler)
        self.assertTrue(handler.pattern.match("followup:pdf"))
        self.assertFalse(handler.pattern.match("followup:steps"))


if __name__ == "__main__":
    unittest.main()
