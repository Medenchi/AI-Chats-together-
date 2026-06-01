"""Personality generator."""
import random
import json
import logging
import re
import ast
from typing import Dict, Any, Optional
from ai.client import AIClient

logger = logging.getLogger(__name__)


class PersonalityGenerator:
    NAMES_M = ["Артём","Даниил","Максим","Иван","Александр","Михаил","Дмитрий","Кирилл","Андрей","Егор",
               "Никита","Тимофей","Роман","Лев","Марк","Степан"]
    NAMES_F = ["Анна","Мария","Елена","Алиса","Софья","Виктория","Дарья","Полина","Екатерина","Александра",
               "Валерия","Ксения","Марина","Ольга","Татьяна","Юлия","Настя","Лиза"]
    TRAITS = ["весёлый","грустный","энергичный","спокойный","мечтательный","практичный",
              "романтичный","саркастичный","добродушный","замкнутый","общительный","творческий",
              "аналитический","импульсивный","осторожный","уверенный","скромный","любопытный",
              "ленивый","амбициозный"]
    HOBBIES = ["рисование","программирование","чтение","музыка","спорт","фотография",
               "путешествия","кулинария","видеоигры","танцы","писательство","настольные игры",
               "аниме","блогинг"]
    BACKSTORIES = ["переехал в новый город","недавно сменил школу","увлекается наукой",
                   "мечтает стать музыкантом","любит природу","живёт с бабушкой",
                   "имеет старшего брата","обожает кино","коллекционирует марки",
                   "занимается волонтёрством","играет в шахматы","ведёт дневник"]

    def __init__(self, ai_client: AIClient):
        self.ai_client = ai_client

    async def generate_personality(self, model: str, age: int = None, gender: str = None) -> Dict[str, Any]:
        age = age or random.randint(16, 18)
        gender = gender or random.choice(["male", "female"])
        name = random.choice(self.NAMES_M if gender == "male" else self.NAMES_F)

        prompt = (f"Персонаж: {name}, {age} лет, {gender}.\n"
                  f"1. 3-4 черты из: {', '.join(self.TRAITS)}\n"
                  f"2. 2 хобби из: {', '.join(self.HOBBIES)}\n"
                  f"3. 1 факт: {', '.join(self.BACKSTORIES)}\n"
                  f"4. Стиль общения (1-2 предложения)\n"
                  f'Верни ТОЛЬКО JSON: {{"name":"{name}","age":{age},"gender":"{gender}",'
                  f'"traits":[],"hobbies":[],"backstory":"","communication_style":""}}')
        try:
            resp = await self.ai_client.generate_with_retry(
                model=model, messages=[{"role": "user", "content": prompt}],
                max_tokens=300, temperature=0.9)
            p = self._parse_json(resp)
            if p:
                return p
        except Exception as e:
            logger.error("AI personality gen failed: %s", e)
        return {"name": name, "age": age, "gender": gender,
                "traits": random.sample(self.TRAITS, 3),
                "hobbies": random.sample(self.HOBBIES, 2),
                "backstory": random.choice(self.BACKSTORIES),
                "communication_style": "обычное дружелюбное общение"}

    def _parse_json(self, text: str) -> Optional[Dict]:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return None

    def get_system_prompt(self, p: Dict[str, Any]) -> str:
        name = p.get('name', 'Персонаж')
        age = p.get('age', '?')
        traits = ', '.join(p.get('traits', ['дружелюбный']))
        hobbies = ', '.join(p.get('hobbies', ['общение']))
        backstory = p.get('backstory', 'обычный человек')
        style = p.get('communication_style', 'обычное дружелюбное общение')
        return (f"Ты — {name}, {age} лет.\n"
                f"Черты: {traits}.\n"
                f"Хобби: {hobbies}.\n"
                f"{backstory}.\n"
                f"Стиль: {style}\n\n"
                "Общайся естественно. Можешь не ответить если нет настроения. "
                "Все персонажи вымышленные.")

    @staticmethod
    def parse_personality_string(s: str) -> Dict[str, Any]:
        """Безопасно парсит строковое представление personality из БД."""
        if not s or s == "{}":
            return {}
        # Сначала пробуем JSON
        try:
            return json.loads(s)
        except (json.JSONDecodeError, TypeError):
            pass
        # Потом ast.literal_eval (безопасная альтернатива eval)
        try:
            return ast.literal_eval(s)
        except (ValueError, SyntaxError):
            pass
        return {}
