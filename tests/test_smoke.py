def test_paquete_importable() -> None:
    import tweet_extractor

    assert tweet_extractor.__version__
