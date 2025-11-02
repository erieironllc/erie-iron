from erieiron_common import common
from erieiron_common.enums import PromptIntent

MAP_INTENT_INTENTDESC = {
    PromptIntent.UNKNOWN: "NONE of these options"
}

SYSTEM_COMMAND_TYPE_TO_EXAMPLE_PHRASE = {
}

TRY_THIS_OPTIONS = [
]

EMPTY_RESPONSE = ""

POSITIVE_RESPONSES = ["yes", "yep", "yeah", "yup", "affirmative", "sure", "indeed", "certainly", "ok", "okay", "sure", "alright", "fine", "alrighty", "k", "cool", "yep", "thanks", "thank you", "thx", "ty", "much appreciated", "thankful", "grateful"]
NEGATIVE_RESPONSES = ["no", "nope", "nah", "negative"]

RECOMMENDATIONS_TRIGGER_WORDS = [
    "hear that",
    "hear it",
    "lets hear",
    "hear an example",
    "sound idea",
    "sound ideas",
    "melody idea",
    "melody ideas",
    "music idea",
    "music ideas",
    "song idea",
    "song ideas",
    "note idea",
    "note ideas",
    "beat idea",
    "beat ideas",
    "what does that sound like",
    "what does it sound like"
]

THANKS = [
    "Thanks",
    "Cool thank you",
    "Awesome - thanks"
]

PLEASE_WAITS = [
    "Cool hang tight",
    "Cool just a moment",
    "I’m on it, give me one sec here",
    "One moment, I'm on it"
]

AFFIRMATIVES = [
    "Awesome",
    "Cool",
    "Dig it",
    "I got you"
]

NON_AFFIRMATIVES = [
    "Shoot",
    "Bummer"
]


def is_question(text):
    if common.is_empty(text):
        return False
    
    if text.endswith('?'):
        return True
    
    # Tokenize and POS tag the text
    from nltk import pos_tag, tokenize
    tokens = tokenize.word_tokenize(text)
    pos_tags = pos_tag(tokens)
    
    # Check if the sentence starts with a modal verb or WH-word
    if pos_tags[0][1] in ['MD', 'WRB', 'WP', 'WDT', 'WP$']:
        return True
    
    # Check if there is a modal verb followed by a verb, a common question structure
    for i in range(len(pos_tags) - 1):
        if pos_tags[i][1] == 'MD' and pos_tags[i + 1][1].startswith('VB'):
            return True
        # Check for auxiliary verb + pronoun + verb (e.g., "Is he going")
        if pos_tags[i][1] in ['VBZ', 'VBP', 'VBD', 'VBG'] and pos_tags[i + 1][1] in ['PRP', 'NNP']:
            return True
    
    return False


def word_count(sentence):
    from nltk import word_tokenize
    return len(word_tokenize(sentence.lower()))


def contains_demonstrative_pronouns(sentence):
    demonstrative_pronouns = {"this", "that", "these", "those"}
    from nltk import word_tokenize
    words = word_tokenize(sentence.lower())
    return any(word in demonstrative_pronouns for word in words)


def is_command(sentence):
    from nltk import word_tokenize, pos_tag
    tokens = word_tokenize(sentence)
    
    # Tag the tokens with POS tags
    tagged = pos_tag(tokens)
    
    # Check if the first word is an imperative verb (VB or VBP)
    return tagged[0][1] in ('VB', 'VBP')


def get_text_embedding(text: str):
    from sentence_transformers import SentenceTransformer
    mini_lm_model = SentenceTransformer("all-MiniLM-L6-v2")
    embedding = mini_lm_model.encode(text, normalize_embeddings=True)
    return embedding
