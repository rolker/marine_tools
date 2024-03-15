#include <rclcpp/rclcpp.hpp>
#include <marine_acoustic_msgs/msg/raw_sonar_image.hpp>
#include <pcl/point_cloud.h>
#include <pcl/point_types.h>
#include "pcl_conversions/pcl_conversions.h"
#include <sensor_msgs/msg/point_cloud2.hpp>

#include "ping.h"

rclcpp::Publisher<sensor_msgs::msg::PointCloud2>::SharedPtr pointcloud_publisher;
float detection_threshold = 0.0;

float last_increment = 0.0;

void sonarPingCallback(const marine_acoustic_msgs::msg::RawSonarImage &msg)
{
  if(!msg.image.data.empty() && msg.image.dtype == marine_acoustic_msgs::msg::SonarImageData::DTYPE_FLOAT32)
  {
    marine_tools::Ping ping(msg);

    pcl::PointCloud<pcl::PointXYZI> pc;
    pc.header.frame_id = msg.header.frame_id;
    pc.header.stamp = rclcpp::Time(msg.header.stamp).nanoseconds()/1000;

    // float start_range = 0.5*msg->ping_info.sound_speed*msg->sample0/msg->sample_rate;
    // float range_increment = 0.5*msg->ping_info.sound_speed/msg->sample_rate;
    const auto& db_re_background = ping.valuesReBackground();
    for(int i = 0; i < db_re_background.size(); i++)
    {
      //float value = reinterpret_cast<const float*>(msg->image.data.data())[i];
      float value = db_re_background[i];
      if(value > detection_threshold)
      {
        //auto range = start_range+ i*range_increment;
        float range = i*ping.binSize();
        if (range >= 15)
        {
          pcl::PointXYZI p;
          p.x = 0.0;
          p.y = 0.0;
          p.z = range;
          p.intensity = value;
          pc.push_back(p);
        }
      }
    }

    sensor_msgs::msg::PointCloud2 pc2;
    pcl::toROSMsg(pc, pc2);
    pointcloud_publisher->publish(pc2);
  }
}

int main(int argc, char* argv[])
{
  rclcpp::init(argc, argv);
  
  auto node = rclcpp::Node::make_shared("marine_sonar_to_pointcloud");

  node->declare_parameter("detection_threshold", 0.0);

  detection_threshold = node->get_parameter("detection_threshold").as_double();

  auto sonar_subsciber = node->create_subscription<marine_acoustic_msgs::msg::RawSonarImage>("sonar", 10, &sonarPingCallback);

  pointcloud_publisher = node->create_publisher<sensor_msgs::msg::PointCloud2>("pointcloud", 10);

  rclcpp::spin(node);
  return 0;
}    
