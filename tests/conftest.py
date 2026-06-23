import sys
import types


if "dotenv" not in sys.modules:
    dotenv = types.ModuleType("dotenv")

    def load_dotenv(*args, **kwargs):
        return False

    dotenv.load_dotenv = load_dotenv
    sys.modules["dotenv"] = dotenv
