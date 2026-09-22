"""Body-to-world rotation and uniform primitive inertia used during packing."""


def rotate(q, p):
    w, x, y, z = q
    u = [y*p[2]-z*p[1], z*p[0]-x*p[2], x*p[1]-y*p[0]]
    v = [y*u[2]-z*u[1], z*u[0]-x*u[2], x*u[1]-y*u[0]]
    return [p[i]+2*(w*u[i]+v[i]) for i in range(3)]


def inertia(entity: dict) -> list[float]:
    """Principal moments about COM, or the declared principal-axis pivot."""
    mass, shape = entity["mass"], entity["shape"]
    if shape["type"] == "sphere":
        values = [0.4*mass*shape["radius"]**2]*3
    elif shape["type"] == "box":
        a, b, c = shape["size"]
        values = [mass*(b*b+c*c)/12, mass*(a*a+c*c)/12, mass*(a*a+b*b)/12]
    else:
        r, h = shape["radius"], shape["height"]
        values = [mass*(3*r*r+h*h)/12, .5*mass*r*r, mass*(3*r*r+h*h)/12]
    if entity.get("pivot"):
        offset = entity["pivot"]["local_point"]
        values = [v + mass*sum(offset[j]**2 for j in range(3) if j != i) for i, v in enumerate(values)]
    return values
