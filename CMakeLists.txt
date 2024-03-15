cmake_minimum_required(VERSION 3.5.1)

project(marine_tools)

set(CMAKE_CXX_STANDARD 17)
if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()


find_package(ament_cmake REQUIRED)
find_package(marine_acoustic_msgs REQUIRED)
find_package(pcl_ros REQUIRED)
find_package(rclcpp REQUIRED)

add_executable(marine_sonar_to_pointcloud src/marine_sonar_to_pointcloud.cpp src/ping.cpp)



ament_target_dependencies(marine_sonar_to_pointcloud
    marine_acoustic_msgs
    pcl_ros
    rclcpp
)


install(TARGETS  marine_sonar_to_pointcloud
    DESTINATION lib/${PROJECT_NAME}
)

ament_export_dependencies(
    marine_acoustic_msgs
    pcl_ros
)

ament_package()
