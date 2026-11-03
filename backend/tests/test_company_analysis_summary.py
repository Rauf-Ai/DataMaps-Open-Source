import unittest
from unittest.mock import AsyncMock, patch

from app.services import company_analysis_service
from app.services.company_analysis_service import build_analysis_prompt, summarize_company_analysis


class CompanyAnalysisSummaryTest(unittest.IsolatedAsyncioTestCase):
    def test_prompt_requires_review_evidence_for_strengths_and_weaknesses(self):
        self.assertIn("только по текстам отзывов", company_analysis_service._ANALYSIS_SYSTEM)

    def test_prompt_is_bounded_for_large_review_sets(self):
        result = {
            "companies": [{
                "company": {"name": "Компания"},
                "reviews": [{"rating": 5, "text": "Очень хороший сервис " * 200} for _ in range(500)],
                "services": ["Услуга"] * 100,
                "prices": ["1000 ₽"] * 100,
                "web": {"text": "Сайт " * 5000},
            }]
        }

        prompt = build_analysis_prompt(result)

        self.assertLessEqual(len(prompt), 30000)

    def test_prompt_includes_bounded_public_website_and_channel_posts(self):
        prompt = build_analysis_prompt({
            "companies": [{
                "company": {"name": "Компания"},
                "reviews": [],
                "web": {"pages": [{"title": "О компании", "text": "Текст сайта"}]},
                "telegram": {"title": "TG", "posts": [{"text": "Пост Telegram", "media": [{"type": "photo"}]}]},
                "vk": {"title": "VK", "posts": [{"text": "Пост ВКонтакте", "media": [{"type": "video"}]}]},
            }]
        })

        self.assertIn("Текст сайта", prompt)
        self.assertIn("Пост Telegram", prompt)
        self.assertIn("Пост ВКонтакте", prompt)

    async def test_llm_error_is_returned_without_destroying_data(self):
        with patch(
            "app.services.company_analysis_service.chat_json",
            new=AsyncMock(return_value=(None, "rate-limit")),
        ):
            summary, error = await summarize_company_analysis({"companies": [{"company": {"name": "A"}}]})

        self.assertIsNone(summary)
        self.assertEqual(error, "rate-limit")

    async def test_valid_llm_summary_is_preserved(self):
        expected = {"overview": "Рынок активный", "recommendations": ["Улучшить сайты"]}
        with patch(
            "app.services.company_analysis_service.chat_json",
            new=AsyncMock(return_value=(expected, None)),
        ):
            summary, error = await summarize_company_analysis({"companies": [{"company": {"name": "A"}}]})

        self.assertEqual(summary["overview"], expected["overview"])
        self.assertEqual(summary["recommendations"], expected["recommendations"])
        self.assertIsNone(error)

    async def test_summary_marks_strengths_and_weaknesses_unavailable_without_reviews(self):
        with patch(
            "app.services.company_analysis_service.chat_json",
            new=AsyncMock(return_value=({"overview": "Карточка собрана"}, None)),
        ):
            summary, error = await summarize_company_analysis({
                "companies": [{"company": {"name": "A"}, "reviews": []}],
            })

        self.assertEqual(
            summary["strengths"],
            ["Недостаточно отзывов для вывода о сильных сторонах."],
        )
        self.assertEqual(
            summary["weaknesses"],
            ["Недостаточно отзывов для вывода о слабых сторонах."],
        )
        self.assertIsNone(error)

    async def test_summary_converts_structured_ai_values_to_text(self):
        with patch(
            "app.services.company_analysis_service.chat_json",
            new=AsyncMock(return_value=({
                "overview": {"text": "Карточка собрана"},
                "recommendations": [{"title": "Обновить сайт"}],
                "outliers": [{"name": "Стомус"}],
            }, None)),
        ):
            summary, error = await summarize_company_analysis({"companies": [{"company": {"name": "A"}}]})

        self.assertEqual(summary["overview"], "Карточка собрана")
        self.assertEqual(summary["recommendations"], ["Обновить сайт"])
        self.assertEqual(summary["outliers"], ["Стомус"])
        self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
