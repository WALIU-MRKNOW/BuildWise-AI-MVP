import unittest

from telegram.ext import CallbackQueryHandler

from bot.handlers.student_learning_handlers import StudentLearningHandlers


class _FakeApplication:
    def __init__(self) -> None:
        self.handlers = []

    def add_handler(self, handler) -> None:
        self.handlers.append(handler)


class StudentPdfHandlerRegistrationTests(unittest.TestCase):
    def test_student_learning_callbacks_are_specific(self) -> None:
        app = _FakeApplication()
        StudentLearningHandlers().register(app)  # type: ignore[arg-type]

        self.assertEqual(len(app.handlers), 2)
        self.assertTrue(all(isinstance(handler, CallbackQueryHandler) for handler in app.handlers))

        pdf_handler, copy_handler = app.handlers
        self.assertTrue(pdf_handler.pattern.match("followup:pdf"))
        self.assertFalse(pdf_handler.pattern.match("followup:steps"))
        self.assertFalse(pdf_handler.pattern.match("followup:copy"))

        self.assertTrue(copy_handler.pattern.match("followup:copy"))
        self.assertFalse(copy_handler.pattern.match("followup:steps"))
        self.assertFalse(copy_handler.pattern.match("followup:pdf"))


if __name__ == "__main__":
    unittest.main()
