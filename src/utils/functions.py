


async def pop_from_dict(dictionary: dict, keys: [str]) -> dict:
    for key in keys:
        dictionary.pop(key, None)
    return dictionary
