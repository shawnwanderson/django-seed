import random

from django.db.models import ForeignKey, ManyToManyField, OneToOneField
from django.db.models.fields import AutoField

from django_seed.exceptions import SeederException
from django_seed.guessers import NameGuesser, FieldTypeGuesser

class ModelSeeder(object):
    def __init__(self, model):
        """
        :param model: Generator
        """
        self.model = model
        self.field_formatters = {}
        self.many_relations = {}

    @staticmethod
    def build_relation(field, related_model):
        def func(inserted):
            if related_model in inserted and inserted[related_model]:
                pk = random.choice(inserted[related_model])
                return related_model.objects.get(pk=pk)
            elif not field.null:
                message = 'Field {} cannot be null'.format(field)
                raise SeederException(message)

        return func

    @staticmethod
    def build_one_relation(field, related_model, existing):
        def func(inserted):
            if related_model in inserted and inserted[related_model]:
                unused = list(set(inserted[related_model]) - existing)
                if unused:
                    pk = random.choice(unused)
                    existing.add(pk)
                    return related_model.objects.get(pk=pk)

            if not field.null:
                message = 'Field {} cannot be null'.format(field)
                raise SeederException(message)

        return func

    @staticmethod
    def build_many_relation(field, related_model):
        def func(inserted):
            if related_model in inserted and inserted[related_model]:
                max_relations = min(10, round(len(inserted[related_model]) / 5) + 1)
                return [
                    related_model.objects.get(pk=random.choice(inserted[related_model]))
                    for _ in range(random.randint(0, max_relations))
                ]
            elif not field.blank:
                message = 'Field {} cannot be null'.format(field)
                raise SeederException(message)

        return func

    def guess_field_formatters(self, faker):
        """
        Gets the formatter methods for each field using the guessers
        or related object fields
        :param faker: Faker factory object
        """
        formatters = {}
        name_guesser = NameGuesser(faker)
        field_type_guesser = FieldTypeGuesser(faker)

        for field in self.model._meta.fields:

            field_name = field.name

            if field.get_default(): 
                formatters[field_name] = field.get_default()
                continue
            
            if isinstance(field, OneToOneField):
                existing = set()
                formatters[field_name] = self.build_one_relation(field, field.related_model, existing)
                continue

            if isinstance(field, ForeignKey):
                formatters[field_name] = self.build_relation(field, field.related_model)
                continue

            if isinstance(field, AutoField):
                continue

            if not field.choices:
                formatter = name_guesser.guess_format(field_name)
                if formatter:
                    formatters[field_name] = formatter
                    continue

            formatter = field_type_guesser.guess_format(field)
            if formatter:
                formatters[field_name] = formatter
                continue

        for field in self.model._meta.many_to_many:
            self.many_relations[field.name] = self.build_many_relation(field, field.related_model)

        return formatters

    def execute(self, using, inserted_entities):
        """
        Execute the stages entities to insert
        :param using:
        :param inserted_entities:
        """

        def format_field(format, inserted_entities):
            if callable(format):
                return format(inserted_entities)
            return format

        def turn_off_auto_add(model):
            for field in model._meta.fields:
                if getattr(field, 'auto_now', False):
                    field.auto_now = False
                if getattr(field, 'auto_now_add', False):
                    field.auto_now_add = False

        manager = self.model.objects.db_manager(using=using)
        turn_off_auto_add(manager.model)

        faker_data = {
            field: format_field(field_format, inserted_entities)
            for field, field_format in self.field_formatters.items()
        }

        # max length restriction check
        for data_field in faker_data:
            field = self.model._meta.get_field(data_field)

            if field.max_length and isinstance(faker_data[data_field], str):
                faker_data[data_field] = faker_data[data_field][:field.max_length]

        obj = manager.create(**faker_data)

        for field, list in self.many_relations.items():
            list = list(inserted_entities)
            if list:
                for related_obj in list:
                    getattr(obj, field).add(related_obj)

        return obj.pk


class Seeder(object):
    def __init__(self, faker):
        """
        :param faker: Generator
        """
        self.faker = faker
        self.orders = []

    def add_entity(self, model, number, customFieldFormatters=None):
        """
        Add an order for the generation of $number records for $entity.

        :param model: mixed A Django Model classname,
        or a faker.orm.django.EntitySeeder instance
        :type model: Model
        :param number: int The number of entities to seed
        :type number: integer
        :param customFieldFormatters: optional dict with field as key and 
        callable as value
        :type customFieldFormatters: dict or None
        """
        if not isinstance(model, ModelSeeder):
            model = ModelSeeder(model)

        model.field_formatters = model.guess_field_formatters(self.faker)
        if customFieldFormatters:
            model.field_formatters.update(customFieldFormatters)
        
        self.orders.append({
            'klass': model.model,
            'number': number,
            'model': model,
        })

    def execute(self, using=None, inserted_entities={}):
        """
        Populate the database using all the Entity classes previously added.

        :param using A Django database connection name
        :rtype: A list of the inserted PKs
        """
        if not using:
            using = self.get_connection()

        for order in self.orders:
            klass = order['klass']
            number = order['number']

            if klass not in inserted_entities:
                inserted_entities[klass] = []
            for i in range(0, number):
                entity = order['model'].execute(using, inserted_entities)
                inserted_entities[klass].append(entity)

        return inserted_entities

    def get_connection(self):
        """
        use the first connection available
        :rtype: Connection
        """

        klass = [order['klass'] for order in self.orders]
        if not klass:
            message = 'No classed found. Did you add entities to the Seeder?'
            raise SeederException(message)
        klass = list(klass)[0]

        return klass.objects._db
