import sys; sys.path.insert(0, "/Users/mckay/i446-monorepo/tools/ibx")
import thread_translator as t
print(repr(t.translate("test for translation", "zh", {})))
print(repr(t.translate("阿珊今天晚上能不能做鱼", "en", {"阿珊": "Kelly"})))
print("done")
