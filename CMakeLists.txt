cmake_minimum_required(VERSION 2.8.3)
project(marine_tools)

find_package(catkin REQUIRED COMPONENTS
    geometry_msgs
    marine_acoustic_msgs
    pcl_ros
    project11_msgs
    rosbag
    roscpp
    tf2
    tf2_ros
    visualization_msgs
)

catkin_package(
    CATKIN_DEPENDS
        geometry_msgs
        marine_acoustic_msgs
        pcl_ros
        project11_msgs
        roscpp
        tf2
        tf2_ros
        visualization_msgs
    INCLUDE_DIRS 
    #include
)

include_directories(
    #include
    ${catkin_INCLUDE_DIRS}
)


add_executable(marine_sonar_to_pointcloud src/marine_sonar_to_pointcloud.cpp src/ping.cpp)
add_dependencies(marine_sonar_to_pointcloud ${marine_tools_EXPORTED_TARGETS} ${catkin_EXPORTED_TARGETS})
target_link_libraries(marine_sonar_to_pointcloud ${catkin_LIBRARIES})

install(TARGETS  marine_sonar_to_pointcloud
        LIBRARY DESTINATION ${CATKIN_PACKAGE_LIB_DESTINATION}
        ARCHIVE DESTINATION ${CATKIN_PACKAGE_LIB_DESTINATION}
        RUNTIME DESTINATION ${CATKIN_GLOBAL_BIN_DESTINATION}
)
