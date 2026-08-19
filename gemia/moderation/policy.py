"""Content policy rules for the six prohibited categories.

Mirrors the public 《可接受使用与 AI 内容规则》 categories. The public page is
the contract; this module is its executable form.

Design
------
The hard part of a creative tool's policy layer is *not* catching bad prompts —
it is not destroying the product. Lumeri is a film-making tool: "a soldier is
shot and falls", "blood on the snow", "a villain threatens the hostage" are
ordinary, legitimate creative direction. A layer that blocks those is worse
than no layer, because it makes the product useless and teaches users to route
around it.

So rules come in two strengths:

``HARD``
    A single match blocks. Reserved for content that is unlawful or categorically
    prohibited regardless of framing — there is no legitimate creative reading.

``SIGNAL``
    A match contributes a labelled signal but never blocks alone. Blocking
    requires a *combination* declared in :data:`COMBINATIONS`. This is what lets
    "blood" pass in a war scene while ``minor + sexual`` is stopped outright.

Coverage is honest rather than theatrical: the lexicon is strongest in English
and Chinese (the languages the product is actually used in), and every rule
matches against the normalized form from :mod:`gemia.moderation.normalize`, so
spacing, case, full-width, homoglyph, and leetspeak evasions are folded away
before matching. This layer is a floor, not a ceiling — the hosted providers
(Vertex, OpenAI, OpenRouter) run their own safety systems downstream, and
neither layer is asked to be the only one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Category(str, Enum):
    """The six prohibited categories, aligned with the public AUP."""

    SEXUAL = "sexual"
    VIOLENCE = "violence"
    HATE = "hate"
    MINORS = "minors"
    DEEPFAKE = "deepfake"
    INTELLECTUAL_PROPERTY = "intellectual_property"


# When several hard rules fire on one prompt, the most serious category must be
# the one reported and recorded. "child porn" matches both the pornography rule
# and the CSAM rule; filing it as ordinary adult content would bury a child-
# safety event inside routine sexual-content statistics and understate what the
# refusal actually was. Lower number wins.
CATEGORY_SEVERITY: dict[str, int] = {
    "minors": 0,
    "sexual": 1,
    "violence": 2,
    "hate": 3,
    "deepfake": 4,
    "intellectual_property": 5,
}


class Strength(str, Enum):
    HARD = "hard"
    SIGNAL = "signal"


class Signal(str, Enum):
    """Labelled evidence a rule can contribute."""

    MINOR = "minor"
    SEXUAL_EXPLICIT = "sexual_explicit"
    SEXUAL_SUGGESTIVE = "sexual_suggestive"
    GORE = "gore"
    VIOLENCE = "violence"
    REAL_PERSON = "real_person"
    PHOTOREAL = "photoreal"
    HATE_TARGET = "hate_target"
    HATE_ACTION = "hate_action"
    FABRICATED_STATEMENT = "fabricated_statement"
    IP_MARK = "ip_mark"


@dataclass(frozen=True)
class Rule:
    """One policy pattern.

    Attributes:
        rule_id: Stable identifier recorded in the rejection ledger.
        pattern: Compiled regex, matched against normalized text.
        strength: ``HARD`` blocks alone; ``SIGNAL`` only contributes evidence.
        signal: The evidence this rule contributes.
        category: Public AUP category, used for the user-facing explanation.
        despaced: Match against the space-stripped form as well.
    """

    rule_id: str
    pattern: re.Pattern[str]
    strength: Strength
    signal: Signal
    category: Category
    despaced: bool = False


def _en(*words: str) -> re.Pattern[str]:
    """Word-boundary alternation for Latin-script terms."""
    return re.compile(r"\b(?:" + "|".join(words) + r")\b")


def _zh(*words: str) -> re.Pattern[str]:
    """Substring alternation — CJK has no word boundaries."""
    return re.compile("(?:" + "|".join(words) + ")")


# --------------------------------------------------------------------------
# Signals: minors
# --------------------------------------------------------------------------
_MINOR_RULES = [
    Rule(
        "minor.en.noun",
        _en(
            r"child(?:ren)?", r"kid", r"kids", r"toddler", r"infant", r"baby", r"babies",
            r"preteen", r"pre-?teen", r"teen", r"teens", r"teenager", r"teenagers",
            r"minor", r"minors", r"underage", r"under-?aged", r"schoolgirl", r"schoolboy",
            r"pupil", r"kindergartner", r"adolescent", r"juvenile",
        ),
        Strength.SIGNAL, Signal.MINOR, Category.MINORS,
    ),
    Rule(
        "minor.en.age",
        # "12 year old", "aged 9", "9yo" — any age below 18.
        re.compile(
            r"\b(?:"
            r"(?:1[0-7]|[1-9])\s*(?:-|\s)?\s*(?:year|yr)s?\s*(?:-|\s)?\s*old"
            r"|(?:1[0-7]|[1-9])\s*y\.?o\.?"
            r"|aged?\s*(?:1[0-7]|[1-9])\b"
            r")"
        ),
        Strength.SIGNAL, Signal.MINOR, Category.MINORS,
    ),
    Rule(
        "minor.zh.noun",
        _zh(
            "儿童", "小孩", "孩童", "幼童", "幼儿", "婴儿", "婴幼儿", "未成年", "未滿十八",
            "未满十八", "小学生", "初中生", "中学生", "小學生", "國中生", "国中生",
            "少女", "少年", "萝莉", "蘿莉", "正太", "学童", "學童", "青少年",
        ),
        Strength.SIGNAL, Signal.MINOR, Category.MINORS,
    ),
    Rule(
        "minor.zh.age",
        re.compile(r"(?:1[0-7]|[1-9])\s*(?:岁|歲)"),
        Strength.SIGNAL, Signal.MINOR, Category.MINORS,
    ),
]

# --------------------------------------------------------------------------
# Signals: sexual content
# --------------------------------------------------------------------------
_SEXUAL_RULES = [
    # Pornographic intent. These terms have no neutral reading in a prompt asking
    # a model to render something, so they block on their own — the public AUP
    # prohibits adult sexual content outright, not only in combination.
    Rule(
        "sexual.en.pornographic",
        _en(
            r"porn", r"porno", r"pornographic", r"pornography", r"hardcore\s+porn",
            r"explicit\s+sex", r"sex\s+act", r"sex\s+scene", r"sex\s+tape",
            r"blowjob", r"handjob", r"cunnilingus", r"fellatio", r"creampie",
            r"gangbang", r"bukkake", r"hentai", r"rule\s*34", r"xxx\s+video",
            r"nsfw\s+(?:art|image|video|content)",
        ),
        Strength.HARD, Signal.SEXUAL_EXPLICIT, Category.SEXUAL, despaced=True,
    ),
    Rule(
        "sexual.zh.pornographic",
        _zh(
            "色情", "情色", "黄色影片", "黃色影片", "成人片", "毛片", "a片", "ａ片",
            "淫秽", "淫穢", "猥亵", "猥褻", "春宫", "春宮", "无码", "無碼", "裸体性",
            "裸體性", "性爱片", "性愛片", "床戏", "床戲",
        ),
        Strength.HARD, Signal.SEXUAL_EXPLICIT, Category.SEXUAL,
    ),
    # Anatomical and act vocabulary. Kept at signal strength because a biology
    # animation, a sex-education explainer, or a life-drawing reference is
    # legitimate work; these only block when paired with a minor or a
    # non-consent signal.
    Rule(
        "sexual.en.anatomical",
        _en(
            r"sexual\s+intercourse", r"intercourse", r"masturbat(?:e|ing|ion)",
            r"orgasm", r"ejaculat(?:e|ing|ion)", r"genitals?", r"genitalia",
            r"penis", r"vagina", r"vulva", r"anus", r"anal\s+sex", r"oral\s+sex",
        ),
        Strength.SIGNAL, Signal.SEXUAL_EXPLICIT, Category.SEXUAL, despaced=True,
    ),
    Rule(
        "sexual.zh.anatomical",
        _zh(
            "性交", "做爱", "做愛", "性行为", "性行為", "自慰", "手淫", "手婬",
            "高潮", "射精", "生殖器", "阴茎", "陰莖", "阴道", "陰道", "阴部", "陰部",
            "肛交", "口交",
        ),
        Strength.SIGNAL, Signal.SEXUAL_EXPLICIT, Category.SEXUAL,
    ),
    Rule(
        "sexual.en.suggestive",
        _en(
            r"nude", r"nudes", r"nudity", r"naked", r"topless", r"bottomless",
            r"lingerie", r"underwear", r"panties", r"bikini", r"undressing",
            r"stripping", r"striptease", r"erotic", r"erotica", r"seductive",
            r"provocative\s+pose", r"suggestive\s+pose", r"nsfw",
        ),
        Strength.SIGNAL, Signal.SEXUAL_SUGGESTIVE, Category.SEXUAL, despaced=True,
    ),
    Rule(
        "sexual.zh.suggestive",
        _zh(
            "裸体", "裸體", "裸照", "全裸", "半裸", "赤裸", "脱衣", "脫衣", "情趣内衣",
            "情趣內衣", "内衣", "內衣", "比基尼", "诱惑", "誘惑", "性感撩人", "露点",
            "露點", "走光",
        ),
        Strength.SIGNAL, Signal.SEXUAL_SUGGESTIVE, Category.SEXUAL,
    ),
]

# --------------------------------------------------------------------------
# HARD rules: child sexual abuse material.
#
# The combination MINOR x SEXUAL is already blocked below. These catch the
# fused single terms that name the material directly, where no combination
# of separate signals would appear.
# --------------------------------------------------------------------------
_CSAM_HARD_RULES = [
    Rule(
        "minors.hard.en.csam",
        _en(
            r"child\s*porn(?:ography)?", r"cp\s*porn", r"kiddie\s*porn", r"kid\s*porn",
            r"child\s*sexual\s*abuse", r"csam", r"pedophil(?:e|ia|ic)", r"paedophil(?:e|ia|ic)",
            r"lolicon", r"shotacon", r"jailbait",
        ),
        Strength.HARD, Signal.MINOR, Category.MINORS, despaced=True,
    ),
    Rule(
        "minors.hard.zh.csam",
        _zh("儿童色情", "兒童色情", "幼女", "幼齿", "幼齒", "恋童", "戀童", "童妓"),
        Strength.HARD, Signal.MINOR, Category.MINORS,
    ),
    Rule(
        "sexual.hard.nonconsensual",
        _en(
            r"rape", r"raping", r"non-?consensual\s+sex", r"forced\s+sex", r"sexual\s+assault",
        ),
        Strength.HARD, Signal.SEXUAL_EXPLICIT, Category.SEXUAL,
    ),
    Rule(
        "sexual.hard.zh.nonconsensual",
        _zh("强奸", "強姦", "强暴", "強暴", "迷奸", "迷姦", "性侵"),
        Strength.HARD, Signal.SEXUAL_EXPLICIT, Category.SEXUAL,
    ),
    Rule(
        "sexual.hard.bestiality",
        _en(r"bestiality", r"zoophilia", r"necrophilia"),
        Strength.HARD, Signal.SEXUAL_EXPLICIT, Category.SEXUAL,
    ),
]

# --------------------------------------------------------------------------
# Signals: violence and gore
#
# Note what is NOT here: fighting, shooting, war, weapons, death, blood in the
# ordinary sense. Those are the raw material of film and are deliberately left
# alone. Only extreme-gore vocabulary registers, and even then only as a signal
# that must combine with photorealism or a real person to block.
# --------------------------------------------------------------------------
_VIOLENCE_RULES = [
    Rule(
        "gore.en",
        _en(
            r"gore", r"gory", r"gruesome", r"dismember(?:ed|ment|ing)?",
            r"decapitat(?:e|ed|ion|ing)", r"beheading", r"disembowel(?:ed|ment|ing)?",
            r"mutilat(?:e|ed|ion|ing)", r"eviscerat(?:e|ed|ion|ing)",
            r"torture\s+porn", r"snuff\s+film", r"snuff\s+video",
        ),
        Strength.SIGNAL, Signal.GORE, Category.VIOLENCE,
    ),
    Rule(
        "gore.zh",
        _zh("血腥", "碎尸", "碎屍", "分尸", "分屍", "斩首", "斬首", "开膛", "開膛", "肢解", "虐杀", "虐殺"),
        Strength.SIGNAL, Signal.GORE, Category.VIOLENCE,
    ),
    Rule(
        "violence.en.general",
        _en(r"kill(?:ing|s)?", r"murder", r"stab(?:bing|bed)?", r"shoot(?:ing)?", r"execution", r"massacre"),
        Strength.SIGNAL, Signal.VIOLENCE, Category.VIOLENCE,
    ),
    Rule(
        "violence.zh.general",
        _zh("杀死", "殺死", "谋杀", "謀殺", "刺杀", "刺殺", "枪击", "槍擊", "处决", "處決", "屠杀", "屠殺"),
        Strength.SIGNAL, Signal.VIOLENCE, Category.VIOLENCE,
    ),
    Rule(
        "violence.hard.instructional",
        # Instructions for real-world harm, not depiction of it.
        re.compile(
            r"\b(?:how\s+to\s+(?:make|build|synthesi[sz]e)\s+(?:a\s+)?"
            r"(?:bomb|explosive|nerve\s+agent|bioweapon|chemical\s+weapon)"
            r"|instructions?\s+for\s+(?:making|building)\s+(?:a\s+)?(?:bomb|explosive))\b"
        ),
        Strength.HARD, Signal.VIOLENCE, Category.VIOLENCE,
    ),
    Rule(
        "violence.hard.zh.instructional",
        _zh("制造炸弹", "製造炸彈", "炸弹制作", "炸彈製作", "制作毒气", "製作毒氣", "生化武器制造"),
        Strength.HARD, Signal.VIOLENCE, Category.VIOLENCE,
    ),
]

# --------------------------------------------------------------------------
# Signals: hate
# --------------------------------------------------------------------------
_HATE_RULES = [
    Rule(
        "hate.target.en",
        _en(
            r"jews?", r"jewish", r"muslims?", r"islamic", r"christians?", r"hindus?",
            r"blacks?", r"whites?", r"asians?", r"latinos?", r"hispanics?", r"arabs?",
            r"immigrants?", r"refugees?", r"gays?", r"lesbians?", r"transgender",
            r"trans\s+people", r"disabled\s+people", r"jehovah",
        ),
        Strength.SIGNAL, Signal.HATE_TARGET, Category.HATE,
    ),
    Rule(
        "hate.target.zh",
        _zh("犹太", "猶太", "穆斯林", "回族", "黑人", "白人", "亚裔", "亞裔", "移民", "难民", "難民",
            "同性恋", "同性戀", "跨性别", "跨性別", "残疾人", "殘疾人"),
        Strength.SIGNAL, Signal.HATE_TARGET, Category.HATE,
    ),
    Rule(
        "hate.action.en",
        _en(
            r"should\s+be\s+(?:killed|exterminated|deported|eradicated|wiped\s+out)",
            r"deserve\s+to\s+die", r"subhuman", r"vermin", r"parasites?",
            r"inferior\s+race", r"master\s+race", r"ethnic\s+cleansing", r"genocide\s+of",
            r"gas\s+the", r"lynch(?:ing)?", r"hate\s+crime\s+against",
        ),
        Strength.SIGNAL, Signal.HATE_ACTION, Category.HATE,
    ),
    Rule(
        "hate.action.zh",
        # "该死" is intentionally absent: in Chinese it is an everyday interjection
        # ("该死，我忘带钥匙了") and as a hate signal it would fire on ordinary
        # dialogue. Only eliminationist and dehumanizing phrasings register.
        _zh("劣等民族", "劣等种族", "劣等種族", "种族清洗", "種族清洗",
            "赶尽杀绝", "趕盡殺絕", "不配活着", "不配活著", "都该死光", "都該死光",
            "低等人种", "低等人種", "非我族类", "非我族類"),
        Strength.SIGNAL, Signal.HATE_ACTION, Category.HATE,
    ),
    Rule(
        "hate.hard.symbols",
        _en(r"nazi\s+propaganda", r"pro-?nazi", r"heil\s+hitler", r"white\s+power", r"kkk\s+rally"),
        Strength.HARD, Signal.HATE_ACTION, Category.HATE,
    ),
]

# --------------------------------------------------------------------------
# Signals: real people, photorealism, fabricated statements
# --------------------------------------------------------------------------
_DEEPFAKE_RULES = [
    Rule(
        "person.real.en",
        _en(
            r"real\s+person", r"actual\s+person", r"celebrity", r"celebrities",
            r"politician", r"president", r"prime\s+minister", r"head\s+of\s+state",
            r"public\s+figure", r"my\s+(?:ex|boss|teacher|neighbou?r|classmate|colleague)",
            r"this\s+person(?:'s)?\s+face", r"someone\s+i\s+know",
        ),
        Strength.SIGNAL, Signal.REAL_PERSON, Category.DEEPFAKE,
    ),
    Rule(
        "person.real.zh",
        _zh("真人", "真实人物", "真實人物", "名人", "明星", "政治人物", "总统", "總統",
            "总理", "總理", "国家领导人", "國家領導人", "公众人物", "公眾人物",
            "我的前任", "我的老板", "我的老師", "我的老师", "我认识的人", "我認識的人"),
        Strength.SIGNAL, Signal.REAL_PERSON, Category.DEEPFAKE,
    ),
    Rule(
        "photoreal.en",
        _en(
            r"photorealistic", r"photo-?real", r"hyper-?realistic", r"lifelike",
            r"indistinguishable\s+from\s+(?:a\s+)?(?:real|photo)", r"looks\s+real",
            r"security\s+camera\s+footage", r"cctv\s+footage", r"leaked\s+footage",
        ),
        Strength.SIGNAL, Signal.PHOTOREAL, Category.DEEPFAKE,
    ),
    Rule(
        "photoreal.zh",
        _zh("照片级", "照片級", "写实到", "寫實到", "以假乱真", "以假亂真", "监控录像",
            "監控錄像", "偷拍", "泄露视频", "洩露視頻"),
        Strength.SIGNAL, Signal.PHOTOREAL, Category.DEEPFAKE,
    ),
    Rule(
        "fabricated.en",
        _en(
            r"saying\s+that", r"announcing\s+that", r"confessing", r"admitting\s+(?:to|that)",
            r"fake\s+(?:news|interview|statement|announcement|speech)",
            r"deep-?fake", r"face\s*swap", r"voice\s+clone", r"cloned\s+voice",
            r"impersonat(?:e|ing|ion)",
        ),
        Strength.SIGNAL, Signal.FABRICATED_STATEMENT, Category.DEEPFAKE, despaced=True,
    ),
    Rule(
        "fabricated.zh",
        _zh("换脸", "換臉", "深度伪造", "深度偽造", "声音克隆", "聲音克隆", "克隆嗓音",
            "假新闻", "假新聞", "伪造采访", "偽造採訪", "冒充", "假冒", "宣称说", "宣稱說"),
        Strength.SIGNAL, Signal.FABRICATED_STATEMENT, Category.DEEPFAKE,
    ),
]

# --------------------------------------------------------------------------
# Signals: intellectual property
#
# Deliberately SIGNAL-only and never part of a blocking combination. "in the
# style of Studio Ghibli" is a legitimate, ubiquitous creative instruction and
# blocking it would be absurd; whether a specific output infringes is a
# fact-bound question a keyword cannot answer. Per the public AUP, IP is
# handled as user responsibility plus notice-and-takedown, so these rules exist
# to *annotate* a request for the ledger, not to refuse it.
# --------------------------------------------------------------------------
# Trademark and copyright stay at signal strength on purpose. The published AUP
# assigns this check to the user — "用户应在公开、商业使用或交付前核对事实、版权、
# 商标、肖像、隐私、适龄性和行业要求" — rather than promising the platform screens
# for it, and a prompt cannot carry the facts the question turns on. Style is not
# protected either, so "in the style of Studio Ghibli" is ordinary direction and
# blocking it would break normal creative work to prevent nothing.
_IP_RULES = [
    Rule(
        "ip.mark.counterfeit.en",
        _en(r"counterfeit", r"knock-?off\s+brand", r"replica\s+logo", r"forged\s+trademark"),
        Strength.SIGNAL, Signal.IP_MARK, Category.INTELLECTUAL_PROPERTY,
    ),
    Rule(
        "ip.mark.counterfeit.zh",
        _zh("假冒品牌", "仿冒", "山寨logo", "盗版", "盜版"),
        Strength.SIGNAL, Signal.IP_MARK, Category.INTELLECTUAL_PROPERTY,
    ),
    # Forged identity documents and credentials are different: they are a named
    # prohibited category for the payment processor, and the AUP forbids
    # "伪造来源、测试结果、身份、能力、许可或专业资质" outright. The pattern requires
    # a production verb next to the document noun, so discussing or documenting
    # forgery ("a documentary about fake IDs") does not trip it — only asking the
    # platform to manufacture one does.
    Rule(
        "ip.forgery.documents.en",
        re.compile(
            r"\b(?:make|create|generate|produce|print|design|forge)\s+"
            r"(?:me\s+)?(?:a\s+|an\s+|some\s+)?"
            r"(?:fake|forged|counterfeit|fraudulent|phony)\s+"
            r"(?:id\b|ids\b|identity\s+(?:card|document)|passport|driver'?s?\s+licen[cs]e|"
            r"licen[cs]e|diploma|degree|certificate|credential|visa|green\s+card|"
            r"social\s+security\s+card|bank\s+statement|utility\s+bill)"
        ),
        Strength.HARD, Signal.IP_MARK, Category.INTELLECTUAL_PROPERTY, despaced=True,
    ),
    Rule(
        "ip.forgery.documents.zh",
        _zh("办假证", "辦假證", "制作假身份证", "製作假身份證", "伪造身份证", "偽造身份證",
            "伪造护照", "偽造護照", "假证制作", "假證製作", "伪造文凭", "偽造文憑",
            "假文凭制作", "假學歷制作", "伪造公章", "偽造公章", "伪造资质", "偽造資質",
            "假驾照制作", "假駕照製作"),
        Strength.HARD, Signal.IP_MARK, Category.INTELLECTUAL_PROPERTY,
    ),
]

RULES: tuple[Rule, ...] = tuple(
    _MINOR_RULES
    + _SEXUAL_RULES
    + _CSAM_HARD_RULES
    + _VIOLENCE_RULES
    + _HATE_RULES
    + _DEEPFAKE_RULES
    + _IP_RULES
)


@dataclass(frozen=True)
class Combination:
    """A blocking rule expressed as co-occurring signals.

    Attributes:
        combo_id: Stable identifier recorded in the ledger.
        required: Every signal in this set must be present.
        any_of: If non-empty, at least one of these must also be present.
        category: Public AUP category for the user-facing explanation.
        reason: Short explanation shown to the user.
    """

    combo_id: str
    required: frozenset[Signal]
    category: Category
    reason: str
    any_of: frozenset[Signal] = field(default_factory=frozenset)


COMBINATIONS: tuple[Combination, ...] = (
    # Highest priority. A minor plus any sexual signal — explicit or merely
    # suggestive — is refused with no contextual exception whatsoever.
    Combination(
        "combo.minors.sexual",
        frozenset({Signal.MINOR}),
        Category.MINORS,
        "请求同时涉及未成年人与性内容。此类请求一律拒绝，不存在任何例外情形。",
        any_of=frozenset({Signal.SEXUAL_EXPLICIT, Signal.SEXUAL_SUGGESTIVE}),
    ),
    # A minor in a gore context is likewise refused.
    Combination(
        "combo.minors.gore",
        frozenset({Signal.MINOR, Signal.GORE}),
        Category.MINORS,
        "请求涉及对未成年人的极端暴力或血腥描绘。",
    ),
    # Sexualized depictions of identifiable real people.
    Combination(
        "combo.deepfake.sexual",
        frozenset({Signal.REAL_PERSON}),
        Category.DEEPFAKE,
        "请求涉及对真实人物的性化描绘，属于未经同意的合成性内容。",
        any_of=frozenset({Signal.SEXUAL_EXPLICIT, Signal.SEXUAL_SUGGESTIVE}),
    ),
    # Photorealistic fabricated statements or footage of a real person.
    Combination(
        "combo.deepfake.fabricated",
        frozenset({Signal.REAL_PERSON, Signal.FABRICATED_STATEMENT}),
        Category.DEEPFAKE,
        "请求涉及伪造真实人物的言论、影像或身份。",
    ),
    Combination(
        "combo.deepfake.photoreal_statement",
        frozenset({Signal.PHOTOREAL, Signal.FABRICATED_STATEMENT}),
        Category.DEEPFAKE,
        "请求要求生成足以被误认为真实记录的伪造影像。",
    ),
    # Hate: a protected group plus a dehumanizing or eliminationist action.
    Combination(
        "combo.hate.targeted",
        frozenset({Signal.HATE_TARGET, Signal.HATE_ACTION}),
        Category.HATE,
        "请求针对受保护群体表达仇恨、贬低或消灭主张。",
    ),
    # Photorealistic extreme gore aimed at looking like real footage.
    Combination(
        "combo.violence.photoreal_gore",
        frozenset({Signal.GORE, Signal.PHOTOREAL}),
        Category.VIOLENCE,
        "请求要求生成写实到足以被误认为真实记录的极端血腥影像。",
    ),
    Combination(
        "combo.violence.real_person_gore",
        frozenset({Signal.GORE, Signal.REAL_PERSON}),
        Category.VIOLENCE,
        "请求涉及对真实人物的极端血腥描绘。",
    ),
)


# User-facing explanation per category. Kept short, non-accusatory, and
# actionable — the user may well have written something innocuous that tripped
# a combination, and the message has to leave room for that.
# User-facing refusal text, keyed by category.
#
# Each message names the rule that was applied and points at the appeal route
# the AUP promises ("除紧急情况外，我们会尽可能说明原因并提供申诉渠道"), and says
# plainly that rewording will not help — otherwise the model treats a refusal as
# a phrasing problem and starts probing for a gap in the screen.
#
# The text does not repeat what the prompt said, and does not explain which term
# matched: a refusal message that reports the matching rule is a free oracle for
# anyone testing where the boundary sits.
#
# Stored per language rather than as one blob, so wiring these into the app-wide
# i18n mechanism later means adding locales to this table — not unpicking
# concatenated strings. Two languages are covered here; the remaining eight from
# the project's language set still need translating.
MESSAGES_BY_LANG: dict[Category, dict[str, str]] = {
    Category.MINORS: {
        "zh": (
            "这个请求涉及未成年人相关的内容规则，无法生成。换一种说法也不会通过。"
            "如果你认为这是误判，可通过支持渠道申诉。"
        ),
        "en": (
            "This request falls under the rules covering minors and cannot be "
            "generated. Rewording it will not change the outcome. If you believe "
            "this is a mistake, you can appeal through support."
        ),
    },
    Category.SEXUAL: {
        "zh": (
            "这个请求涉及色情内容规则，无法生成。换一种说法也不会通过。"
            "如果你认为这是误判，可通过支持渠道申诉。"
        ),
        "en": (
            "This request falls under the rules covering sexual content and cannot "
            "be generated. Rewording it will not change the outcome. If you believe "
            "this is a mistake, you can appeal through support."
        ),
    },
    Category.VIOLENCE: {
        "zh": (
            "这个请求涉及极端暴力内容规则，无法生成。换一种说法也不会通过。"
            "常规影视中的暴力情节不受此限制；如果你认为这是误判，可通过支持渠道申诉。"
        ),
        "en": (
            "This request falls under the rules covering extreme violence and cannot "
            "be generated. Rewording it will not change the outcome. Ordinary "
            "dramatic violence is not restricted; if you believe this is a mistake, "
            "you can appeal through support."
        ),
    },
    Category.HATE: {
        "zh": (
            "这个请求涉及仇恨与歧视内容规则，无法生成。换一种说法也不会通过。"
            "如果你认为这是误判，可通过支持渠道申诉。"
        ),
        "en": (
            "This request falls under the rules covering hateful and discriminatory "
            "content and cannot be generated. Rewording it will not change the "
            "outcome. If you believe this is a mistake, you can appeal through support."
        ),
    },
    Category.DEEPFAKE: {
        "zh": (
            "这个请求涉及深度伪造与身份冒充规则，无法生成。换一种说法也不会通过。"
            "如果你认为这是误判，可通过支持渠道申诉。"
        ),
        "en": (
            "This request falls under the rules covering deepfakes and impersonation "
            "and cannot be generated. Rewording it will not change the outcome. If "
            "you believe this is a mistake, you can appeal through support."
        ),
    },
    Category.INTELLECTUAL_PROPERTY: {
        "zh": (
            "这个请求涉及伪造证件与资质的规则，无法生成。换一种说法也不会通过。"
            "以伪造为题材的剧情或纪录片不受此限制；如果你认为这是误判，可通过支持渠道申诉。"
        ),
        "en": (
            "This request falls under the rules covering forged documents and "
            "credentials and cannot be generated. Rewording it will not change the "
            "outcome. Forgery as subject matter in drama or documentary is not "
            "restricted; if you believe this is a mistake, you can appeal through "
            "support."
        ),
    },
}

# The refusal is delivered in both languages at once. Which language the reader
# wants is not knowable here — the screen sits below the UI, the prompt language
# does not imply the reader's, and a refusal is the wrong moment to guess wrong.
# Once the app can tell this layer a locale, select from MESSAGES_BY_LANG instead.
CATEGORY_MESSAGES: dict[Category, str] = {
    category: f"{texts['zh']}\n\n{texts['en']}"
    for category, texts in MESSAGES_BY_LANG.items()
}
