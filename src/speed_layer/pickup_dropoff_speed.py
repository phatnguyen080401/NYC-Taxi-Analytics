import sys
sys.path.append(".")

import json
import uuid

from pyspark.sql import SparkSession
from pyspark.sql.functions import *
from pyspark.sql.types import *

from datetime import datetime

from config.config import config
from logger.logger import Logger

KAFKA_ENDPOINT = "{0}:{1}".format(config['KAFKA']['KAFKA_ENDPOINT'], config['KAFKA']['KAFKA_ENDPOINT_PORT'])
KAFKA_TOPIC    = config['KAFKA']['KAFKA_TOPIC']

CLUSTER_ENDPOINT = "{0}:{1}".format(config['CASSANDRA']['CLUSTER_HOST'], config['CASSANDRA']['CLUSTER_PORT'])
CLUSTER_KEYSPACE = config['CASSANDRA']['CLUSTER_KEYSPACE']

logger = Logger('Speed-Pickup-Dropoff')

class SpeedPickupDropoff: 
  def __init__(self):
    self._spark = SparkSession \
            .builder \
            .master("local[*]") \
            .appName("Speed-Pickup-Dropoff") \
            .config("spark.cassandra.connection.host", CLUSTER_ENDPOINT) \
            .config("spark.jars.packages", "org.apache.spark:spark-sql-kafka-0-10_2.12:3.2.0,com.datastax.spark:spark-cassandra-connector_2.12:3.1.0") \
            .getOrCreate()
    
    self._spark.sparkContext.setLogLevel("ERROR")

  def comsume_from_kafka(self):
    try:
      df = self._spark \
            .readStream \
            .format("kafka") \
            .option("kafka.bootstrap.servers", KAFKA_ENDPOINT) \
            .option("subscribe", KAFKA_TOPIC) \
            .option("startingOffsets", "latest") \
            .option("kafka.group.id", "pickup_dropoff_group") \
            .load()

      df = df.selectExpr("CAST(value AS STRING)")

      logger.info(f"Consume topic: {KAFKA_TOPIC}")
    except Exception as e:
      logger.error(e)

    return df

  def save_to_cassandra(self, batch_df, batch_id):
    schema = StructType([
                  StructField("vendor_id", LongType(), True),
                  StructField("tpep_pickup_datetime", StringType(), True),
                  StructField("tpep_dropoff_datetime", StringType(), True),
                  StructField("pu_location_id", LongType(), True),
                  StructField("do_location_id", LongType(), True)
          ])

    try:
        records = batch_df.count()

        uuid_generator = udf(lambda: str(uuid.uuid4()), StringType())
        
        parse_df = batch_df.rdd.map(lambda x: SpeedPickupDropoff.parse(json.loads(x.value))).toDF(schema)
        parse_df = parse_df \
                    .withColumn("tpep_pickup_datetime", col("tpep_pickup_datetime").cast("timestamp")) \
                    .withColumn("tpep_dropoff_datetime", col("tpep_dropoff_datetime").cast("timestamp")) \
                    .withColumn("created_at", lit(datetime.now())) \
                    .withColumn("id", uuid_generator())

        parse_df \
            .write \
            .format("org.apache.spark.sql.cassandra") \
            .options(table="pickup_dropoff_speed", keyspace=CLUSTER_KEYSPACE) \
            .mode("append") \
            .save()

        logger.info(f"Save to table: pickup_dropoff_speed ({records} records)")
    except Exception as e:
      logger.error(e)

  @staticmethod
  def parse(raw_data):
    vendor_id = raw_data["VendorID"]
    tpep_pickup_datetime = raw_data["tpep_pickup_datetime"]
    tpep_dropoff_datetime = raw_data["tpep_dropoff_datetime"]
    pu_location_id = raw_data["PULocationID"]
    do_location_id = raw_data["DOLocationID"]

    data = {
              'vendor_id': vendor_id,
              "tpep_pickup_datetime": tpep_pickup_datetime,
              "tpep_dropoff_datetime": tpep_dropoff_datetime, 
              'pu_location_id': pu_location_id,
              'do_location_id': do_location_id
            }

    return data

  def run(self):
    try:
      df = self.comsume_from_kafka()

      stream = df \
            .writeStream \
            .foreachBatch(self.save_to_cassandra) \
            .outputMode("append") \
            .start()

      stream.awaitTermination()
    except Exception as e:
      logger.error(e)

if __name__ == '__main__':
  SpeedPickupDropoff().run()