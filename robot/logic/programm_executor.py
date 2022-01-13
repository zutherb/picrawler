import nltk
from nltk import word_tokenize

try:
  print(nltk.pos_tag(word_tokenize("Number one sit")))
  print(nltk.pos_tag(word_tokenize("Number one sit down")))
  print(nltk.pos_tag(word_tokenize("Number one please sit down")))
  print(nltk.pos_tag(word_tokenize("Number one could please sit down")))
  print(nltk.pos_tag(word_tokenize("Number one stand up")))
  print(nltk.pos_tag(word_tokenize("Number one dance")))
except LookupError as error:
  print(type(error))
  print(str(error))
  nltk.download('averaged_perceptron_tagger')
  nltk.download('punkt')
