#!/usr/bin/env python3

import rospy
import sys
from nmea_msgs.msg import Sentence
from std_msgs.msg import String

rospy.init_node("string_to_nmea", sys.argv)

nmea_publisher = rospy.Publisher("nmea", Sentence, queue_size=10)

def stringCallback(msg: String):
    now = rospy.Time.now()
    for s in msg.data.split():
        sentence = Sentence()
        sentence.header.stamp = now
        sentence.sentence = s
        nmea_publisher.publish(sentence)

string_subscriber = rospy.Subscriber("input", String, stringCallback, queue_size=10)

rospy.spin()
