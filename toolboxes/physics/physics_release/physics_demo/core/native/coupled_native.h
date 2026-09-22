#ifndef PHYSICS_COUPLED_NATIVE_H
#define PHYSICS_COUPLED_NATIVE_H
#include "physics_native.h"
#include "mesh_native.h"
#include "rigid_math.h"
#define COUPLED_ABI 1u
typedef struct { rm_vec3 position,velocity; double inverse_mass,radius; uint32_t field_mask; } CoupledPoint;
typedef struct {
    rm_body body;
    rm_vec3 size,pivot_point,pivot_local;
    double radius,height,mass,friction,restitution;
    int32_t shape,fixed,pivot;
} CoupledRigid;
/* endpoint kind: 0 point, 1 mesh vertex, 2 rigid body local point */
typedef struct { int32_t kind,index; rm_vec3 local_point; } CoupledEndpoint;
typedef struct { int32_t type; CoupledEndpoint a,b; double rest,stiffness,damping,radius; } CoupledLink;
/* entity kind: 0 particle range, 1 point, 2 mesh object, 3 rigid */
typedef struct { int32_t kind,start,count; } CoupledEntity;
/* metric: radius=0,centroid=1,speed=2,distance=3,volume=4,displacement=5,
 * strain=6,length=7,extension=8,force=9,spring_energy=10,
 * angular_speed=11,angular_momentum(axis)=12,rotational_energy=13,axis_tilt=14. */
typedef struct { int32_t type,a,b,axis0,axis1; rm_vec3 origin; } CoupledMetric;
typedef struct {
    uint32_t abi;
    int32_t points,rigids,links,entities,metrics,fields,colliders;
    int32_t substeps,iterations,frame_capacity,observation_capacity;
    double dt,duration,fps,deadline_seconds,friction;
    rm_vec3 gravity;
    PhySimulation *particles;
    MeshSimulation *mesh;
    CoupledPoint *point;
    CoupledRigid *rigid;
    CoupledLink *link;
    CoupledEntity *entity;
    CoupledMetric *metric;
    PhyForceField *field;
    PhyCollider *collider;
    /* Particle and mesh frame buffers live in their sub-simulation structs. */
    double *point_frames,*rigid_frames,*frame_times,*observation_times,*observation_values;
} CoupledSimulation;
typedef struct {
    int32_t status,completed,finite,frames_written,observations_written,steps,substeps,max_substeps_used,contact_count;
    double simulated_time_s,runtime_s,max_penetration_m,max_speed_m_s,max_quaternion_error;
    double contact_impulse_norm,attachment_impulse_norm,max_link_constraint_error;
    rm_vec3 initial_momentum,final_momentum,support_impulse;
} CoupledDiagnostics;
PHY_EXPORT uint32_t coupled_abi_version(void);
PHY_EXPORT int32_t coupled_simulate(CoupledSimulation*,CoupledDiagnostics*,PhyDiagnostics*,MeshDiagnostics*);
#endif
