"""This file holds the StatChange class which makes it easy to display potential changes being made to the database."""

class StatChange:
    def __init__(self, collection, document_filter, player_name, stat_name, old, new):
        self.collection = collection
        self.document_filter = document_filter
        self.player_name = player_name
        self.stat_name = stat_name
        self.old = old
        self.new = new

class FieldNotFound(Exception):
    """A custom exception for alerting when a field is not found."""
    def __init__(self, message):
        super().__init__(message)