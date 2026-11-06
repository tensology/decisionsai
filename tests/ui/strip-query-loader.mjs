export async function resolve(specifier, context, nextResolve) {
    return nextResolve(specifier.split('?')[0], context);
}
